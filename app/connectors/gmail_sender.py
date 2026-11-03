"""Approval-bound Gmail sender with a durable duplicate/ambiguity ledger.

This adapter is only registered by trusted application configuration. Google
Gmail does not provide a general client-supplied idempotency key for message
send, so Alex records a local attempt before network I/O and refuses to resend
when the prior outcome is unresolved. The ledger stores hashes and receipt IDs,
not message bodies or credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
import base64
import hashlib
import os
import sqlite3
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.connectors.email_provider import EmailDeliveryUncertain, EmailProviderError
from app.connectors.google_oauth import GoogleGmailOutcomeUncertain, GoogleOAuthAdapter, GoogleOAuthError
from app.connectors.sandbox_email import SandboxEmailOutbox


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GmailSendLedger:
    """Durable, account-scoped send-key ledger; uncertain keys are never retried."""

    def __init__(self, path: str):
        if not isinstance(path, str) or not path.strip() or path.strip() == ":memory:":
            raise ValueError("A persistent Gmail send-ledger path is required")
        self.path = path.strip()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS gmail_send_ledger (
                account_hash TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('sending','sent','uncertain','failed')),
                attempt_id TEXT NOT NULL,
                execution_id TEXT,
                provider_message_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(account_hash, key_hash)
            )""")
            # Migrate v3.27/v3.28 ledgers without discarding old attempts. Legacy
            # rows remain explicitly unlinked; the system never guesses a match.
            columns = {row[1] for row in db.execute("PRAGMA table_info(gmail_send_ledger)").fetchall()}
            if "execution_id" not in columns:
                db.execute("ALTER TABLE gmail_send_ledger ADD COLUMN execution_id TEXT")
            # A process that died during network I/O cannot know whether Gmail
            # accepted the message. Preserve the key and require reconciliation.
            db.execute("UPDATE gmail_send_ledger SET status='uncertain', updated_at=? WHERE status='sending'", (_now(),))

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, account_id: str, idempotency_key: str, payload_hash: str,
                execution_id: str | None = None) -> dict:
        account_hash = _sha256(account_id)
        key_hash = _sha256(idempotency_key)
        attempt_id = str(uuid4())
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload_hash,status,provider_message_id,attempt_id FROM gmail_send_ledger WHERE account_hash=? AND key_hash=?",
                             (account_hash, key_hash)).fetchone()
            if row is not None:
                old_payload, status, provider_message_id, _ = row
                db.rollback()
                if old_payload != payload_hash:
                    raise EmailProviderError("Idempotency key was already used for a different Gmail message")
                if status == "sent" and provider_message_id:
                    return {"status": "sent", "provider_message_id": provider_message_id, "duplicate_replay": True}
                if status == "failed":
                    raise EmailProviderError("This Gmail idempotency key belongs to a failed attempt; create a new key and obtain a new approval before trying again")
                raise EmailDeliveryUncertain("This Gmail idempotency key already has an unresolved attempt; reconcile before retrying")
            db.execute("INSERT INTO gmail_send_ledger(account_hash,key_hash,payload_hash,status,attempt_id,execution_id,provider_message_id,created_at,updated_at) VALUES (?,?,?,'sending',?,?,NULL,?,?)",
                       (account_hash, key_hash, payload_hash, attempt_id, execution_id, now, now))
            db.commit()
        return {"status": "reserved", "attempt_id": attempt_id, "account_hash": account_hash, "key_hash": key_hash}

    def mark_sent(self, reservation: dict, provider_message_id: str) -> None:
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE gmail_send_ledger SET status='sent',provider_message_id=?,updated_at=? WHERE account_hash=? AND key_hash=? AND attempt_id=? AND status IN ('sending','uncertain')",
                             (provider_message_id, _now(), reservation["account_hash"], reservation["key_hash"], reservation["attempt_id"]))
            if cur.rowcount != 1:
                raise RuntimeError("Gmail send ledger changed before receipt could be recorded")

    def mark_uncertain(self, reservation: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE gmail_send_ledger SET status='uncertain',updated_at=? WHERE account_hash=? AND key_hash=? AND attempt_id=? AND status IN ('sending','uncertain')",
                       (_now(), reservation["account_hash"], reservation["key_hash"], reservation["attempt_id"]))

    def mark_failed(self, reservation: dict) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE gmail_send_ledger SET status='failed',updated_at=? WHERE account_hash=? AND key_hash=? AND attempt_id=? AND status='sending'",
                       (_now(), reservation["account_hash"], reservation["key_hash"], reservation["attempt_id"]))

    def status_for_key(self, account_id: str, idempotency_key: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT status,provider_message_id,created_at,updated_at FROM gmail_send_ledger WHERE account_hash=? AND key_hash=?",
                             (_sha256(account_id), _sha256(idempotency_key))).fetchone()
        if row is None:
            return None
        return {"status": row[0], "provider_message_id": row[1], "created_at": row[2], "updated_at": row[3]}

    def unresolved(self, account_id: str, limit: int = 100) -> list[dict]:
        """Return safe metadata for unresolved sends; never returns keys or message content."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT status,attempt_id,execution_id,provider_message_id,created_at,updated_at "
                "FROM gmail_send_ledger WHERE account_hash=? AND status IN ('sending','uncertain') "
                "ORDER BY created_at DESC LIMIT ?",
                (_sha256(account_id), limit),
            ).fetchall()
        return [{"status": row[0], "attempt_id": row[1], "execution_id": row[2],
                 "provider_message_id": row[3], "created_at": row[4], "updated_at": row[5]} for row in rows]

    def get_attempt(self, account_id: str, attempt_id: str) -> dict | None:
        """Fetch one account-scoped attempt without returning keys or payload hashes."""
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id is required")
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT status,attempt_id,execution_id,provider_message_id,created_at,updated_at "
                "FROM gmail_send_ledger WHERE account_hash=? AND attempt_id=?",
                (_sha256(account_id), attempt_id.strip()),
            ).fetchone()
        if row is None:
            return None
        return {"status": row[0], "attempt_id": row[1], "execution_id": row[2],
                "provider_message_id": row[3], "created_at": row[4], "updated_at": row[5]}


class GoogleGmailEmailProvider:
    """EmailProviderAdapter for one explicitly configured, owner-scoped account."""

    def __init__(self, oauth_adapter: GoogleOAuthAdapter, *, owner_actor_id: str,
                 account_id: str, ledger_path: str):
        if not isinstance(oauth_adapter, GoogleOAuthAdapter):
            raise ValueError("A GoogleOAuthAdapter is required")
        if not isinstance(owner_actor_id, str) or not owner_actor_id.strip():
            raise ValueError("A connected-account owner actor ID is required")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("A Google account ID is required")
        self.oauth_adapter = oauth_adapter
        self.owner_actor_id = owner_actor_id.strip()
        self.account_id = account_id.strip()
        self.ledger = GmailSendLedger(ledger_path)

    def authorization_status(self) -> dict:
        status = self.oauth_adapter.local_status(self.account_id, self.owner_actor_id)
        scopes = status.get("scopes", [])
        has_send_scope = isinstance(scopes, list) and "https://www.googleapis.com/auth/gmail.send" in scopes
        expiry = status.get("expires_at")
        expires_at = None
        if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
            try:
                expires_at = datetime.fromtimestamp(float(expiry), timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                expires_at = None
        return {
            "authorized": status.get("authorized") is True and has_send_scope,
            "account_id": self.account_id,
            "account_label": status.get("account_label"),
            "expires_at": expires_at,
            "scopes": scopes if isinstance(scopes, list) else [],
            "issue": status.get("issue") if status.get("authorized") is not True else (None if has_send_scope else "gmail_send_scope_missing"),
        }

    @staticmethod
    def _encode_message(message: dict[str, str]) -> str:
        # Use the shared validator, then construct headers through EmailMessage
        # to avoid raw header concatenation and CR/LF injection.
        SandboxEmailOutbox.validate({**message, "idempotency_key": "message-validation-key"})
        if "\r" in message["subject"] or "\n" in message["subject"]:
            raise EmailProviderError("Email subject must not contain line breaks")
        email = EmailMessage(policy=SMTP)
        email["To"] = message["to"].strip()
        email["Subject"] = message["subject"].strip()
        email.set_content(message["body"])
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii").rstrip("=")
        if len(raw) > 2_000_000:
            raise EmailProviderError("Encoded email exceeds the Gmail API message limit")
        return raw

    def send_email(self, message: dict[str, str], idempotency_key: str) -> dict:
        return self._send_email(message, idempotency_key, execution_id=None)

    def send_email_with_context(self, message: dict[str, str], idempotency_key: str,
                                execution_context) -> dict:
        execution_id = getattr(execution_context, "execution_id", None)
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise EmailProviderError("Trusted execution context is required for Gmail delivery")
        return self._send_email(message, idempotency_key, execution_id=execution_id.strip())

    def _send_email(self, message: dict[str, str], idempotency_key: str,
                    execution_id: str | None) -> dict:
        status = self.authorization_status()
        if status.get("authorized") is not True:
            raise EmailProviderError("The configured Google account is not authorized for Gmail sending")
        raw = self._encode_message(message)
        payload_hash = _sha256("\0".join((message["to"].strip().lower(), message["subject"].strip(), message["body"])))
        reservation = self.ledger.reserve(self.account_id, idempotency_key, payload_hash,
                                          execution_id=execution_id)
        if reservation["status"] == "sent":
            return {"provider_message_id": reservation["provider_message_id"], "status": "sent", "duplicate_replay": True}
        try:
            receipt = self.oauth_adapter.send_gmail_message(self.account_id, self.owner_actor_id, raw)
            message_id = receipt["provider_message_id"]
            self.ledger.mark_sent(reservation, message_id)
        except GoogleGmailOutcomeUncertain:
            self.ledger.mark_uncertain(reservation)
            raise EmailDeliveryUncertain("Gmail send outcome is uncertain; reconcile before retrying") from None
        except GoogleOAuthError:
            self.ledger.mark_failed(reservation)
            raise EmailProviderError("Gmail send was not completed; inspect account status before creating a new approved action") from None
        except EmailDeliveryUncertain:
            self.ledger.mark_uncertain(reservation)
            raise
        except Exception:
            # This includes a failure to persist the receipt after a provider
            # response; the message may already exist in Gmail.
            self.ledger.mark_uncertain(reservation)
            raise EmailDeliveryUncertain("Gmail may have accepted the message but its receipt could not be durably recorded; reconcile before retrying") from None
        return {"provider_message_id": message_id, "status": "sent", "duplicate_replay": False}
