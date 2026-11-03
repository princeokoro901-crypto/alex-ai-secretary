import base64
import hashlib
import sqlite3

import pytest
from cryptography.fernet import Fernet

from app.connectors.oauth_security import (
    OAuthAuthorizationError,
    OAuthAuthorizationService,
    OAuthCredentialError,
    OAuthCredentialVault,
)


KEY = Fernet.generate_key()
REDIRECTS = {"gmail": "https://app.example.test/oauth/gmail/callback"}


def test_oauth_state_uses_pkce_and_persists_only_state_hash_and_encrypted_verifier(tmp_path):
    db = tmp_path / "oauth.sqlite3"
    service = OAuthAuthorizationService(db, KEY, REDIRECTS)
    started = service.start("gmail", "actor-1", "key-1")

    assert started["code_challenge_method"] == "S256"
    assert "code_verifier" not in started
    with sqlite3.connect(db) as conn:
        state_hash, encrypted_verifier = conn.execute(
            "SELECT state_hash, encrypted_code_verifier FROM oauth_authorization_attempts"
        ).fetchone()
    assert state_hash == hashlib.sha256(started["state"].encode()).hexdigest()
    assert started["state"] not in state_hash
    assert started["state"] not in encrypted_verifier
    assert len(encrypted_verifier) > 50

    consumed = service.consume_callback(
        "gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"]
    )
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(consumed["code_verifier"].encode()).digest()
    ).rstrip(b"=").decode()
    assert expected == started["code_challenge"]
    assert consumed["actor_id"] == "actor-1"
    assert service.status_counts()["consumed"] == 1


def test_oauth_state_is_one_time_and_replay_is_rejected(tmp_path):
    service = OAuthAuthorizationService(tmp_path / "oauth.sqlite3", KEY, REDIRECTS)
    started = service.start("gmail", "actor-1", "key-1")
    service.consume_callback("gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])
    with pytest.raises(OAuthAuthorizationError, match="invalid, expired, or already used"):
        service.consume_callback("gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])


def test_wrong_actor_or_key_does_not_consume_legitimate_state(tmp_path):
    service = OAuthAuthorizationService(tmp_path / "oauth.sqlite3", KEY, REDIRECTS)
    started = service.start("gmail", "actor-1", "key-1")
    with pytest.raises(OAuthAuthorizationError, match="context does not match"):
        service.consume_callback("gmail", started["state"], "actor-2", "key-1", REDIRECTS["gmail"])
    with pytest.raises(OAuthAuthorizationError, match="context does not match"):
        service.consume_callback("gmail", started["state"], "actor-1", "other-key", REDIRECTS["gmail"])
    result = service.consume_callback("gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])
    assert result["actor_id"] == "actor-1"


def test_wrong_provider_or_redirect_uri_is_rejected(tmp_path):
    service = OAuthAuthorizationService(tmp_path / "oauth.sqlite3", KEY, REDIRECTS)
    started = service.start("gmail", "actor-1", "key-1")
    with pytest.raises((OAuthAuthorizationError, ValueError)):
        service.consume_callback("other", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])
    with pytest.raises(OAuthAuthorizationError, match="context does not match"):
        service.consume_callback("gmail", started["state"], "actor-1", "key-1", "https://evil.example/callback")
    service.consume_callback("gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])


def test_expired_oauth_state_cannot_be_consumed(tmp_path):
    now = [1000.0]
    service = OAuthAuthorizationService(tmp_path / "oauth.sqlite3", KEY, REDIRECTS, clock=lambda: now[0])
    started = service.start("gmail", "actor-1", "key-1")
    now[0] = float(started["expires_at"]) + 1
    with pytest.raises(OAuthAuthorizationError, match="invalid, expired, or already used"):
        service.consume_callback("gmail", started["state"], "actor-1", "key-1", REDIRECTS["gmail"])
    assert service.status_counts()["expired"] == 1


def test_redirect_uri_must_be_trusted_https_or_local_development(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        OAuthAuthorizationService(tmp_path / "bad.sqlite3", KEY, {"gmail": "http://evil.example/callback"})
    with pytest.raises(ValueError, match="HTTPS"):
        OAuthAuthorizationService(tmp_path / "bad2.sqlite3", KEY, {"gmail": "https://user:pass@example.test/callback"})
    service = OAuthAuthorizationService(tmp_path / "local.sqlite3", KEY, {"gmail": "http://localhost:8080/callback"})
    assert service.redirect_uris["gmail"].startswith("http://localhost")


def test_credential_vault_encrypts_tokens_and_redacts_status(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    vault = OAuthCredentialVault(db, KEY, clock=lambda: 1000.0)
    vault.store_tokens("gmail", "account-1", "ACCESS_SECRET_123", "REFRESH_SECRET_456", 900.0,
                       ["email.send", "email.read", "email.send"], identity_verified=True, owner_actor_id="actor-1")
    raw = db.read_bytes()
    assert b"ACCESS_SECRET_123" not in raw
    assert b"REFRESH_SECRET_456" not in raw
    status = vault.status("gmail", "account-1", "actor-1")
    assert status["authorized"] is True  # expired access token can be refreshed
    assert status["access_token_expired"] is True
    assert status["refresh_available"] is True
    assert status["scopes"] == ["email.send", "email.read"]
    assert "ACCESS_SECRET_123" not in repr(status)
    assert "REFRESH_SECRET_456" not in repr(status)
    assert vault.get_tokens_for_adapter("gmail", "account-1", "actor-1") == {
        "access_token": "ACCESS_SECRET_123", "refresh_token": "REFRESH_SECRET_456", "expires_at": 900.0
    }


def test_credential_vault_requires_verified_identity_and_safe_token_values(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", KEY)
    with pytest.raises(OAuthCredentialError, match="identity must be verified"):
        vault.store_tokens("gmail", "account-1", "access", "refresh", None, [], identity_verified=False, owner_actor_id="actor-1")
    with pytest.raises(ValueError, match="access_token"):
        vault.store_tokens("gmail", "account-1", " ", None, None, [], identity_verified=True, owner_actor_id="actor-1")


def test_refresh_update_preserves_existing_refresh_token_when_provider_omits_new_one(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", KEY, clock=lambda: 1000.0)
    vault.store_tokens("gmail", "account-1", "ACCESS_OLD", "REFRESH_KEEP", 900.0, ["email.send"], identity_verified=True, owner_actor_id="actor-1")
    vault.store_tokens("gmail", "account-1", "ACCESS_NEW", None, 2000.0, ["email.send"], identity_verified=True, owner_actor_id="actor-1")
    assert vault.get_tokens_for_adapter("gmail", "account-1", "actor-1") == {
        "access_token": "ACCESS_NEW", "refresh_token": "REFRESH_KEEP", "expires_at": 2000.0
    }


def test_revocation_erases_token_ciphertext_and_blocks_retrieval(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    vault = OAuthCredentialVault(db, KEY, clock=lambda: 1000.0)
    vault.store_tokens("gmail", "account-1", "ACCESS_SECRET", "REFRESH_SECRET", 2000.0, ["email.send"], identity_verified=True, owner_actor_id="actor-1")
    assert vault.revoke("gmail", "account-1", "actor-1") is True
    assert vault.revoke("gmail", "account-1", "actor-1") is False
    with pytest.raises(OAuthCredentialError, match="unavailable or revoked"):
        vault.get_tokens_for_adapter("gmail", "account-1", "actor-1")
    status = vault.status("gmail", "account-1", "actor-1")
    assert status["authorized"] is False
    assert status["issue"] == "revoked"
    with sqlite3.connect(db) as conn:
        access, refresh, state = conn.execute(
            "SELECT encrypted_access_token, encrypted_refresh_token, status FROM oauth_credentials"
        ).fetchone()
    assert access is None and refresh is None and state == "revoked"


def test_expired_access_token_without_refresh_token_requires_reauthorization(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", KEY, clock=lambda: 1000.0)
    vault.store_tokens("gmail", "account-1", "ACCESS_ONLY", None, 900.0, ["email.send"], identity_verified=True, owner_actor_id="actor-1")
    status = vault.status("gmail", "account-1", "actor-1")
    assert status["authorized"] is False
    assert status["issue"] == "access_token_expired_reauthorization_required"


def test_invalid_encryption_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Fernet key"):
        OAuthCredentialVault(tmp_path / "credentials.sqlite3", "not-a-valid-key")


def test_vault_rejects_scope_string_instead_of_treating_each_character_as_a_scope(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", KEY)
    with pytest.raises(ValueError, match="iterable of individual scope strings"):
        vault.store_tokens("gmail", "account-1", "ACCESS", None, None, "email.send", identity_verified=True, owner_actor_id="actor-1")


def test_vault_rejects_boolean_expiry_timestamp(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", KEY)
    with pytest.raises(ValueError, match="positive Unix timestamp"):
        vault.store_tokens("gmail", "account-1", "ACCESS", None, True, [], identity_verified=True, owner_actor_id="actor-1")


def test_credential_vault_binds_account_to_owner_and_prevents_cross_actor_access(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "owned-credentials.sqlite3", KEY)
    vault.store_tokens("gmail", "account-1", "ACCESS_SECRET", "REFRESH_SECRET", 2000.0,
                       ["https://www.googleapis.com/auth/gmail.send"],
                       identity_verified=True, owner_actor_id="actor-a")
    assert vault.status("gmail", "account-1", "actor-b")["configured"] is False
    with pytest.raises(OAuthCredentialError, match="unavailable or revoked"):
        vault.get_tokens_for_adapter("gmail", "account-1", "actor-b")
    assert vault.revoke("gmail", "account-1", "actor-b") is False
    with pytest.raises(OAuthCredentialError, match="different actor"):
        vault.store_tokens("gmail", "account-1", "OTHER_ACCESS", None, 3000.0,
                           ["https://www.googleapis.com/auth/gmail.send"],
                           identity_verified=True, owner_actor_id="actor-b")
    assert vault.get_tokens_for_adapter("gmail", "account-1", "actor-a")["access_token"] == "ACCESS_SECRET"


def test_legacy_ownerless_credentials_are_not_visible_to_any_actor(tmp_path):
    db = tmp_path / "legacy-credentials.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE oauth_credentials (
                provider_id TEXT NOT NULL, account_id TEXT NOT NULL,
                encrypted_access_token TEXT, encrypted_refresh_token TEXT, expires_at REAL,
                scopes_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                created_at REAL NOT NULL, updated_at REAL NOT NULL, revoked_at REAL,
                PRIMARY KEY(provider_id, account_id)
            )
        """)
    vault = OAuthCredentialVault(db, KEY)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(oauth_credentials)")}
    assert {"owner_actor_id", "account_label", "refresh_lease_token", "refresh_lease_until"}.issubset(columns)
    assert vault.status("gmail", "legacy-account", "actor-a")["configured"] is False
