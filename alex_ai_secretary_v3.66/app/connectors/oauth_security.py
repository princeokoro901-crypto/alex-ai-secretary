"""Secure primitives for a future provider-specific OAuth integration.

This module intentionally does not perform browser redirects, authorization-code
exchange, token refresh HTTP calls, or email delivery. It provides durable,
one-time OAuth state/PKCE handling and encrypted credential storage for trusted
provider adapters to use after those flows are separately implemented/reviewed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SCOPE_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


class OAuthAuthorizationError(ValueError):
    """An OAuth authorization transaction is invalid, expired, or already used."""


class OAuthCredentialError(RuntimeError):
    """A credential is unavailable, revoked, or could not be safely stored."""


def _provider_id(value: str) -> str:
    if not isinstance(value, str) or not _PROVIDER_ID_RE.fullmatch(value):
        raise ValueError("provider_id must be 2-64 lowercase letters, digits, '_' or '-' and start with a letter")
    return value


def _required_text(value: str, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters")
    return value.strip()


def _validate_redirect_uri(value: str) -> str:
    value = _required_text(value, "redirect_uri", 2048)
    parts = urlsplit(value)
    local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parts.scheme != "https" and not local_http) or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("redirect_uri must be an HTTPS URL (HTTP is allowed only for localhost development)")
    return value


def _fernet(key: str | bytes) -> Fernet:
    if isinstance(key, str):
        key = key.encode("ascii", errors="strict")
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise ValueError("encryption_key must be a valid Fernet key; generate one with Fernet.generate_key()") from exc


class _SQLiteStore:
    def __init__(self, path: str | Path):
        raw_path = str(path)
        if not raw_path.strip() or raw_path == ":memory:":
            raise ValueError("A durable SQLite file path is required")
        self.path = raw_path
        Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn


class OAuthAuthorizationService(_SQLiteStore):
    """Persist one-time OAuth state and encrypted PKCE verifiers.

    Redirect URIs are trusted configuration, never caller-provided callback
    destinations. A callback must return through the same authenticated actor
    and API-key identity that initiated the transaction. The raw state is not
    stored; only its SHA-256 digest is persisted.
    """

    def __init__(self, path: str | Path, encryption_key: str | bytes,
                 redirect_uris: dict[str, str], state_ttl_seconds: int = 600,
                 clock: Callable[[], float] = time.time):
        super().__init__(path)
        self._cipher = _fernet(encryption_key)
        if not isinstance(state_ttl_seconds, int) or not 60 <= state_ttl_seconds <= 1800:
            raise ValueError("state_ttl_seconds must be between 60 and 1800")
        if not isinstance(redirect_uris, dict) or not redirect_uris:
            raise ValueError("redirect_uris must be a non-empty provider-to-URI mapping")
        self.redirect_uris = {_provider_id(pid): _validate_redirect_uri(uri) for pid, uri in redirect_uris.items()}
        self.state_ttl_seconds = state_ttl_seconds
        self._clock = clock
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_authorization_attempts (
                    state_hash TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    encrypted_code_verifier TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','consumed','expired')),
                    consumed_at REAL
                )
            """)
            # Backward-compatible schema migration for v3.25 browser-session binding.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(oauth_authorization_attempts)")}
            if "browser_session_hash" not in columns:
                conn.execute("ALTER TABLE oauth_authorization_attempts ADD COLUMN browser_session_hash TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_oauth_attempt_status_expiry ON oauth_authorization_attempts(status, expires_at)")

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _browser_binding_hash(value: str) -> str:
        if not isinstance(value, str) or not 32 <= len(value) <= 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise OAuthAuthorizationError("OAuth browser binding is invalid")
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    def start(self, provider_id: str, actor_id: str, key_id: str,
              browser_session_binding: str | None = None) -> dict[str, str | int]:
        provider_id = _provider_id(provider_id)
        if provider_id not in self.redirect_uris:
            raise OAuthAuthorizationError("OAuth provider is not configured")
        actor_id = _required_text(actor_id, "actor_id", 200)
        key_id = _required_text(key_id, "key_id", 200)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        now = self._clock()
        expires_at = now + self.state_ttl_seconds
        state_hash = self._digest(state)
        browser_session_hash = (self._browser_binding_hash(browser_session_binding)
                                if browser_session_binding is not None else None)
        encrypted_verifier = self._cipher.encrypt(verifier.encode("ascii")).decode("ascii")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE oauth_authorization_attempts SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
            conn.execute("""
                INSERT INTO oauth_authorization_attempts
                (state_hash, provider_id, actor_id, key_id, redirect_uri, encrypted_code_verifier,
                 created_at, expires_at, status, browser_session_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (state_hash, provider_id, actor_id, key_id, self.redirect_uris[provider_id],
                  encrypted_verifier, now, expires_at, browser_session_hash))
            conn.commit()
        # Never return the verifier to the client; trusted callback code retrieves it internally.
        return {
            "provider_id": provider_id,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": self.redirect_uris[provider_id],
            "expires_at": int(expires_at),
        }

    def consume_callback(self, provider_id: str, state: str, actor_id: str, key_id: str,
                         redirect_uri: str) -> dict[str, str]:
        """Atomically consume a valid state and return callback context to trusted code.

        Call this only inside a future server-side callback handler. Do not expose
        its returned code_verifier in an HTTP response or logs.
        """
        provider_id = _provider_id(provider_id)
        if not isinstance(state, str) or not 32 <= len(state) <= 256:
            raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
        actor_id = _required_text(actor_id, "actor_id", 200)
        key_id = _required_text(key_id, "key_id", 200)
        redirect_uri = _validate_redirect_uri(redirect_uri)
        now = self._clock()
        row_data = None
        expired = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM oauth_authorization_attempts WHERE state_hash=?", (self._digest(state),)).fetchone()
            if row is None or row["status"] != "pending":
                conn.rollback()
                raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
            matches = (
                hmac.compare_digest(row["provider_id"], provider_id)
                and hmac.compare_digest(row["actor_id"], actor_id)
                and hmac.compare_digest(row["key_id"], key_id)
                and hmac.compare_digest(row["redirect_uri"], redirect_uri)
            )
            if not matches:
                conn.rollback()
                raise OAuthAuthorizationError("OAuth callback context does not match the authorization request")
            if row["expires_at"] <= now:
                conn.execute("UPDATE oauth_authorization_attempts SET status='expired' WHERE state_hash=? AND status='pending'", (self._digest(state),))
                conn.commit()
                expired = True
            else:
                try:
                    verifier = self._cipher.decrypt(row["encrypted_code_verifier"].encode("ascii")).decode("ascii")
                except (InvalidToken, UnicodeDecodeError):
                    conn.rollback()
                    raise OAuthAuthorizationError("OAuth transaction could not be safely recovered") from None
                updated = conn.execute("UPDATE oauth_authorization_attempts SET status='consumed', consumed_at=? WHERE state_hash=? AND status='pending'", (now, self._digest(state)))
                if updated.rowcount != 1:
                    conn.rollback()
                    raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
                row_data = {
                    "provider_id": provider_id,
                    "actor_id": actor_id,
                    "key_id": key_id,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
                conn.commit()
        if expired or row_data is None:
            raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
        return row_data

    def consume_callback_browser(self, provider_id: str, state: str,
                                 browser_session_binding: str, redirect_uri: str) -> dict[str, str]:
        """Consume callback state bound to the initiating HttpOnly browser cookie.

        Actor/key identity is recovered only from the trusted server-side row. The
        browser cookie is an opaque random binding, not an authentication credential.
        """
        provider_id = _provider_id(provider_id)
        if not isinstance(state, str) or not 32 <= len(state) <= 256:
            raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
        binding_hash = self._browser_binding_hash(browser_session_binding)
        redirect_uri = _validate_redirect_uri(redirect_uri)
        now = self._clock()
        row_data = None
        expired = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM oauth_authorization_attempts WHERE state_hash=?",
                (self._digest(state),),
            ).fetchone()
            if row is None or row["status"] != "pending":
                conn.rollback()
                raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
            stored_binding = row["browser_session_hash"]
            matches = (
                hmac.compare_digest(row["provider_id"], provider_id)
                and hmac.compare_digest(row["redirect_uri"], redirect_uri)
                and isinstance(stored_binding, str)
                and hmac.compare_digest(stored_binding, binding_hash)
            )
            if not matches:
                conn.rollback()
                raise OAuthAuthorizationError("OAuth callback browser context does not match the authorization request")
            if row["expires_at"] <= now:
                conn.execute(
                    "UPDATE oauth_authorization_attempts SET status='expired' WHERE state_hash=? AND status='pending'",
                    (self._digest(state),),
                )
                conn.commit()
                expired = True
            else:
                try:
                    verifier = self._cipher.decrypt(row["encrypted_code_verifier"].encode("ascii")).decode("ascii")
                except (InvalidToken, UnicodeDecodeError):
                    conn.rollback()
                    raise OAuthAuthorizationError("OAuth transaction could not be safely recovered") from None
                updated = conn.execute(
                    "UPDATE oauth_authorization_attempts SET status='consumed', consumed_at=? WHERE state_hash=? AND status='pending'",
                    (now, self._digest(state)),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
                row_data = {
                    "provider_id": provider_id,
                    "actor_id": row["actor_id"],
                    "key_id": row["key_id"],
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
                conn.commit()
        if expired or row_data is None:
            raise OAuthAuthorizationError("OAuth state is invalid, expired, or already used")
        return row_data

    def status_counts(self) -> dict[str, int]:
        """Return aggregate operational counts only; never return state or verifier data."""
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM oauth_authorization_attempts GROUP BY status").fetchall()
        counts = {"pending": 0, "consumed": 0, "expired": 0}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return counts


class OAuthCredentialVault(_SQLiteStore):
    """Encrypted OAuth vault with owner-bound account and refresh coordination.

    Decrypted token reads are an internal trusted-adapter API only. Public
    status/list methods are redacted. A short SQLite lease prevents concurrent
    workers from refreshing the same account simultaneously.
    """

    def __init__(self, path: str | Path, encryption_key: str | bytes,
                 clock: Callable[[], float] = time.time):
        super().__init__(path)
        self._cipher = _fernet(encryption_key)
        self._clock = clock
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_credentials (
                    provider_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    owner_actor_id TEXT,
                    account_label TEXT,
                    encrypted_access_token TEXT,
                    encrypted_refresh_token TEXT,
                    expires_at REAL,
                    scopes_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    revoked_at REAL,
                    refresh_lease_token TEXT,
                    refresh_lease_until REAL,
                    PRIMARY KEY(provider_id, account_id)
                )
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(oauth_credentials)")}
            migrations = {
                "owner_actor_id": "TEXT",
                "account_label": "TEXT",
                "refresh_lease_token": "TEXT",
                "refresh_lease_until": "REAL",
            }
            for column, declaration in migrations.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE oauth_credentials ADD COLUMN {column} {declaration}")

    @staticmethod
    def _clean_scopes(scopes: Iterable[str]) -> list[str]:
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be an iterable of individual scope strings")
        try:
            scope_items = list(scopes)
        except TypeError as exc:
            raise ValueError("scopes must be an iterable of individual scope strings") from exc
        clean_scopes = []
        for scope in scope_items:
            if not isinstance(scope, str) or not _SCOPE_RE.fullmatch(scope):
                raise ValueError("Each scope must contain only safe scope characters and be at most 160 characters")
            if scope not in clean_scopes:
                clean_scopes.append(scope)
        if len(clean_scopes) > 100:
            raise ValueError("At most 100 scopes may be stored")
        return clean_scopes

    def store_tokens(self, provider_id: str, account_id: str, access_token: str,
                     refresh_token: str | None, expires_at: float | None,
                     scopes: Iterable[str], *, identity_verified: bool, owner_actor_id: str,
                     account_label: str | None = None) -> None:
        """Store verified tokens under one actor; another actor cannot claim the account."""
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        if identity_verified is not True:
            raise OAuthCredentialError("Provider account identity must be verified before storing credentials")
        access_token = _required_text(access_token, "access_token", 16384)
        if refresh_token is not None:
            refresh_token = _required_text(refresh_token, "refresh_token", 16384)
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or expires_at <= 0):
            raise ValueError("expires_at must be a positive Unix timestamp or None")
        if account_label is not None:
            account_label = _required_text(account_label, "account_label", 160)
        clean_scopes = self._clean_scopes(scopes)
        now = self._clock()
        encrypted_access = self._cipher.encrypt(access_token.encode("utf-8")).decode("ascii")
        encrypted_refresh = self._cipher.encrypt(refresh_token.encode("utf-8")).decode("ascii") if refresh_token else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT owner_actor_id FROM oauth_credentials WHERE provider_id=? AND account_id=?",
                (provider_id, account_id),
            ).fetchone()
            if existing is not None and existing["owner_actor_id"] not in (None, owner_actor_id):
                conn.rollback()
                raise OAuthCredentialError("Provider account is already linked to a different actor")
            conn.execute("""
                INSERT INTO oauth_credentials
                (provider_id, account_id, owner_actor_id, account_label, encrypted_access_token,
                 encrypted_refresh_token, expires_at, scopes_json, status, created_at, updated_at,
                 revoked_at, refresh_lease_token, refresh_lease_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, NULL, NULL)
                ON CONFLICT(provider_id, account_id) DO UPDATE SET
                    owner_actor_id=excluded.owner_actor_id,
                    account_label=COALESCE(excluded.account_label, oauth_credentials.account_label),
                    encrypted_access_token=excluded.encrypted_access_token,
                    encrypted_refresh_token=COALESCE(excluded.encrypted_refresh_token, oauth_credentials.encrypted_refresh_token),
                    expires_at=excluded.expires_at,
                    scopes_json=excluded.scopes_json,
                    status='active', updated_at=excluded.updated_at, revoked_at=NULL,
                    refresh_lease_token=NULL, refresh_lease_until=NULL
            """, (provider_id, account_id, owner_actor_id, account_label, encrypted_access,
                  encrypted_refresh, expires_at, json.dumps(clean_scopes), now, now))
            conn.commit()

    def get_tokens_for_adapter(self, provider_id: str, account_id: str, owner_actor_id: str) -> dict[str, str | float | None]:
        """Return decrypted tokens only to trusted adapter code for the owning actor."""
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_credentials WHERE provider_id=? AND account_id=? AND owner_actor_id=?",
                (provider_id, account_id, owner_actor_id),
            ).fetchone()
        if row is None or row["status"] != "active" or not row["encrypted_access_token"]:
            raise OAuthCredentialError("OAuth credentials are unavailable or revoked")
        try:
            access = self._cipher.decrypt(row["encrypted_access_token"].encode("ascii")).decode("utf-8")
            refresh = self._cipher.decrypt(row["encrypted_refresh_token"].encode("ascii")).decode("utf-8") if row["encrypted_refresh_token"] else None
        except (InvalidToken, UnicodeDecodeError):
            raise OAuthCredentialError("OAuth credentials could not be decrypted") from None
        return {"access_token": access, "refresh_token": refresh, "expires_at": row["expires_at"]}

    def _status_payload(self, row: sqlite3.Row) -> dict[str, object]:
        expired = row["expires_at"] is not None and row["expires_at"] <= self._clock()
        refresh_available = bool(row["encrypted_refresh_token"])
        active = row["status"] == "active" and bool(row["encrypted_access_token"])
        authorized = active and (not expired or refresh_available)
        issue = None
        if row["status"] != "active":
            issue = "revoked"
        elif not row["encrypted_access_token"]:
            issue = "credentials_unavailable"
        elif expired and not refresh_available:
            issue = "access_token_expired_reauthorization_required"
        elif expired:
            issue = "access_token_expired_refresh_required"
        refreshing = bool(row["refresh_lease_until"] and row["refresh_lease_until"] > self._clock())
        return {
            "provider_id": row["provider_id"],
            "account_id": row["account_id"],
            "account_label": row["account_label"],
            "configured": True,
            "authorized": authorized,
            "access_token_expired": expired,
            "refresh_available": refresh_available,
            "refresh_in_progress": refreshing,
            "expires_at": row["expires_at"],
            "scopes": json.loads(row["scopes_json"]),
            "issue": issue,
        }

    def status(self, provider_id: str, account_id: str, owner_actor_id: str) -> dict[str, object]:
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_credentials WHERE provider_id=? AND account_id=? AND owner_actor_id=?",
                (provider_id, account_id, owner_actor_id),
            ).fetchone()
        if row is None:
            return {"provider_id": provider_id, "account_id": account_id, "configured": False,
                    "authorized": False, "account_label": None, "issue": "credentials_not_found"}
        return self._status_payload(row)

    def list_accounts_for_owner(self, provider_id: str, owner_actor_id: str) -> list[dict[str, object]]:
        """List only redacted account metadata owned by this actor."""
        provider_id = _provider_id(provider_id)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM oauth_credentials WHERE provider_id=? AND owner_actor_id=? ORDER BY updated_at DESC",
                (provider_id, owner_actor_id),
            ).fetchall()
        return [self._status_payload(row) for row in rows]

    def claim_token_refresh(self, provider_id: str, account_id: str, owner_actor_id: str,
                            *, safety_window_seconds: int = 60, lease_seconds: int = 30) -> dict[str, object]:
        """Atomically claim a refresh lease and return the refresh token to trusted code.

        A lease prevents multiple app workers from concurrently calling the provider
        with a rotating refresh token. Expired leases can be reclaimed after crashes.
        """
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        if isinstance(safety_window_seconds, bool) or not isinstance(safety_window_seconds, int) or not 0 <= safety_window_seconds <= 3600:
            raise ValueError("safety_window_seconds must be between 0 and 3600")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 5 and 300")
        now = self._clock()
        lease_token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM oauth_credentials WHERE provider_id=? AND account_id=? AND owner_actor_id=?",
                (provider_id, account_id, owner_actor_id),
            ).fetchone()
            if row is None or row["status"] != "active" or not row["encrypted_access_token"]:
                conn.rollback()
                raise OAuthCredentialError("OAuth credentials are unavailable or revoked")
            if row["expires_at"] is not None and row["expires_at"] > now + safety_window_seconds:
                conn.rollback()
                return {"status": "not_needed", "expires_at": row["expires_at"]}
            if not row["encrypted_refresh_token"]:
                conn.rollback()
                raise OAuthCredentialError("Access token expired; reauthorization is required")
            if row["refresh_lease_until"] and row["refresh_lease_until"] > now:
                conn.rollback()
                raise OAuthCredentialError("Token refresh is already in progress")
            try:
                refresh_token = self._cipher.decrypt(row["encrypted_refresh_token"].encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError):
                conn.rollback()
                raise OAuthCredentialError("OAuth credentials could not be decrypted") from None
            updated = conn.execute(
                "UPDATE oauth_credentials SET refresh_lease_token=?, refresh_lease_until=? "
                "WHERE provider_id=? AND account_id=? AND owner_actor_id=? AND status='active'",
                (lease_token, now + lease_seconds, provider_id, account_id, owner_actor_id),
            )
            if updated.rowcount != 1:
                conn.rollback()
                raise OAuthCredentialError("Token refresh could not be safely claimed")
            scopes = json.loads(row["scopes_json"])
            conn.commit()
        return {"status": "claimed", "lease_token": lease_token, "refresh_token": refresh_token,
                "scopes": scopes, "lease_until": now + lease_seconds}

    def complete_token_refresh(self, provider_id: str, account_id: str, owner_actor_id: str,
                               lease_token: str, access_token: str, refresh_token: str | None,
                               expires_at: float, scopes: Iterable[str]) -> bool:
        """Persist a refresh only if the caller still owns the active lease."""
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        lease_token = _required_text(lease_token, "lease_token", 256)
        access_token = _required_text(access_token, "access_token", 16384)
        if refresh_token is not None:
            refresh_token = _required_text(refresh_token, "refresh_token", 16384)
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or expires_at <= self._clock():
            raise ValueError("expires_at must be a future Unix timestamp")
        clean_scopes = self._clean_scopes(scopes)
        encrypted_access = self._cipher.encrypt(access_token.encode("utf-8")).decode("ascii")
        encrypted_refresh = self._cipher.encrypt(refresh_token.encode("utf-8")).decode("ascii") if refresh_token else None
        now = self._clock()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT refresh_lease_token FROM oauth_credentials WHERE provider_id=? AND account_id=? "
                "AND owner_actor_id=? AND status='active'",
                (provider_id, account_id, owner_actor_id),
            ).fetchone()
            if row is None or not row["refresh_lease_token"] or not hmac.compare_digest(row["refresh_lease_token"], lease_token):
                conn.rollback()
                return False
            cursor = conn.execute("""
                UPDATE oauth_credentials SET encrypted_access_token=?,
                    encrypted_refresh_token=COALESCE(?, encrypted_refresh_token), expires_at=?,
                    scopes_json=?, updated_at=?, refresh_lease_token=NULL, refresh_lease_until=NULL
                WHERE provider_id=? AND account_id=? AND owner_actor_id=? AND status='active' AND refresh_lease_token=?
            """, (encrypted_access, encrypted_refresh, float(expires_at), json.dumps(clean_scopes), now,
                  provider_id, account_id, owner_actor_id, lease_token))
            if cursor.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
        return True

    def release_token_refresh(self, provider_id: str, account_id: str, owner_actor_id: str,
                              lease_token: str) -> bool:
        """Release a failed refresh lease without changing existing credentials."""
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        lease_token = _required_text(lease_token, "lease_token", 256)
        with self._connect() as conn:
            cursor = conn.execute("""
                UPDATE oauth_credentials SET refresh_lease_token=NULL, refresh_lease_until=NULL
                WHERE provider_id=? AND account_id=? AND owner_actor_id=? AND status='active' AND refresh_lease_token=?
            """, (provider_id, account_id, owner_actor_id, lease_token))
        return cursor.rowcount == 1

    def revoke(self, provider_id: str, account_id: str, owner_actor_id: str) -> bool:
        """Disable local credentials immediately; this does not guarantee provider revocation."""
        provider_id = _provider_id(provider_id)
        account_id = _required_text(account_id, "account_id", 512)
        owner_actor_id = _required_text(owner_actor_id, "owner_actor_id", 200)
        now = self._clock()
        with self._connect() as conn:
            cursor = conn.execute("""
                UPDATE oauth_credentials SET encrypted_access_token=NULL, encrypted_refresh_token=NULL,
                    status='revoked', revoked_at=?, updated_at=?, refresh_lease_token=NULL, refresh_lease_until=NULL
                WHERE provider_id=? AND account_id=? AND owner_actor_id=? AND status='active'
            """, (now, now, provider_id, account_id, owner_actor_id))
        return cursor.rowcount == 1
