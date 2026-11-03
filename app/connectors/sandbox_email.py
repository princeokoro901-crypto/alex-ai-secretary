"""A safe email connector prototype that records messages without sending them.

This module deliberately performs no network calls. It is suitable for local
end-to-end workflow tests until a separately reviewed provider integration exists.
"""
from datetime import datetime, timezone
import hashlib
import os
import re
import sqlite3
from threading import RLock
from uuid import uuid4

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def sandbox_email_action(payload: dict) -> str:
    """Return an approval string bound to message content and deduplication key."""
    SandboxEmailOutbox.validate(payload)
    subject_hash = hashlib.sha256(payload["subject"].strip().encode("utf-8")).hexdigest()
    body_hash = hashlib.sha256(payload["body"].encode("utf-8")).hexdigest()
    idempotency_key = payload.get("idempotency_key")
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() if idempotency_key else "none"
    return f"sandbox.email.send to={payload['to'].strip()} subject_sha256={subject_hash} body_sha256={body_hash} idempotency_key_sha256={key_hash}"


class SandboxEmailOutbox:
    """SQLite outbox for simulated emails; default storage is in-memory."""

    def __init__(self, path: str | None = None):
        self.path = path or ":memory:"
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS sandbox_email_outbox (
                    message_id TEXT PRIMARY KEY,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'simulated_queued'),
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT
                )
            """)
            # Safe additive migration for databases created by v3.20.
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(sandbox_email_outbox)")}
            if "idempotency_key" not in columns:
                self._db.execute("ALTER TABLE sandbox_email_outbox ADD COLUMN idempotency_key TEXT")
            self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sandbox_email_idempotency_key ON sandbox_email_outbox(idempotency_key) WHERE idempotency_key IS NOT NULL")

    @staticmethod
    def validate(payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Email payload must be an object")
        unknown = set(payload) - {"to", "subject", "body", "idempotency_key"}
        if unknown:
            raise ValueError("Unknown email field(s): " + ", ".join(sorted(str(item) for item in unknown)))
        recipient = payload.get("to")
        subject = payload.get("subject")
        body = payload.get("body")
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
            or len(idempotency_key) > 128
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", idempotency_key)
        ):
            raise ValueError("idempotency_key must be 1-128 characters using letters, digits, '.', '_', ':', or '-'")
        if not isinstance(recipient, str) or not _EMAIL_RE.fullmatch(recipient.strip()):
            raise ValueError("A valid recipient email address is required")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("Email subject cannot be empty")
        if "\r" in subject or "\n" in subject:
            raise ValueError("Email subject must not contain line breaks")
        if len(subject) > 300:
            raise ValueError("Email subject must be 300 characters or fewer")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Email body cannot be empty")
        if len(body) > 100_000:
            raise ValueError("Email body must be 100000 characters or fewer")

    def queue_simulated(self, payload: dict) -> dict:
        self.validate(payload)
        idempotency_key = payload.get("idempotency_key")
        message = {
            "message_id": str(uuid4()),
            "recipient": payload["to"].strip(),
            "subject": payload["subject"].strip(),
            "body": payload["body"],
            "status": "simulated_queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": idempotency_key,
        }
        with self._lock, self._db:
            if idempotency_key:
                existing = self._db.execute(
                    "SELECT message_id, recipient, subject, body, status, created_at, idempotency_key FROM sandbox_email_outbox WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    previous = dict(existing)
                    same_payload = (previous["recipient"] == message["recipient"] and
                                    previous["subject"] == message["subject"] and
                                    previous["body"] == message["body"])
                    if not same_payload:
                        raise ValueError("Idempotency key was already used for a different email payload")
                    return {**previous, "mode": "simulation_only", "sent": False, "duplicate_replay": True}
            try:
                self._db.execute(
                    "INSERT INTO sandbox_email_outbox(message_id, recipient, subject, body, status, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (message["message_id"], message["recipient"], message["subject"], message["body"], message["status"], message["created_at"], idempotency_key),
                )
            except sqlite3.IntegrityError:
                # A separate process may have inserted the same key after our lookup.
                if not idempotency_key:
                    raise
                existing = self._db.execute(
                    "SELECT message_id, recipient, subject, body, status, created_at, idempotency_key FROM sandbox_email_outbox WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing is None:
                    raise
                previous = dict(existing)
                if (previous["recipient"], previous["subject"], previous["body"]) != (message["recipient"], message["subject"], message["body"]):
                    raise ValueError("Idempotency key was already used for a different email payload")
                return {**previous, "mode": "simulation_only", "sent": False, "duplicate_replay": True}
        return {**message, "mode": "simulation_only", "sent": False, "duplicate_replay": False}

    def list_messages(self, limit: int = 100) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with self._lock:
            rows = self._db.execute(
                "SELECT message_id, recipient, subject, body, status, created_at, idempotency_key FROM sandbox_email_outbox ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
