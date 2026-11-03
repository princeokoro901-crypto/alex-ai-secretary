from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from app.connectors.google_oauth import (
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_REQUIRED_SCOPES,
    GoogleOAuthAdapter,
    GoogleOAuthError,
)
from app.connectors.oauth_security import OAuthAuthorizationService, OAuthCredentialVault


class FakeTransport:
    def __init__(self, token_response=None, identity=None, error=None, refresh_response=None, revoke_error=None):
        self.token_response = token_response or {
            "access_token": "ACCESS_SECRET",
            "refresh_token": "REFRESH_SECRET",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(GOOGLE_REQUIRED_SCOPES),
        }
        self.refresh_response = refresh_response or {
            "access_token": "ACCESS_REFRESHED",
            "token_type": "Bearer",
            "expires_in": 7200,
        }
        self.identity = identity or {
            "sub": "google-sub-123",
            "email": "prince@example.com",
            "email_verified": True,
        }
        self.error = error
        self.revoke_error = revoke_error
        self.token_calls = []
        self.identity_calls = []
        self.revocation_calls = []
        self.gmail_calls = []
        self.gmail_error = None

    def post_form(self, url, form):
        self.token_calls.append((url, dict(form)))
        if self.error:
            raise self.error
        if form.get("grant_type") == "refresh_token":
            return self.refresh_response
        return self.token_response

    def get_bearer_json(self, url, access_token):
        self.identity_calls.append((url, access_token))
        if self.error:
            raise self.error
        return self.identity

    def revoke_token(self, token):
        self.revocation_calls.append(token)
        if self.revoke_error:
            raise self.revoke_error

    def send_gmail_message(self, access_token, raw_message):
        self.gmail_calls.append((access_token, raw_message))
        if self.gmail_error:
            raise self.gmail_error
        return {"id": "gmail-message-123", "threadId": "thread-123"}


def make_adapter(tmp_path, *, enabled=True, transport=None, clock=lambda: 1000.0):
    key = Fernet.generate_key()
    auth = OAuthAuthorizationService(
        tmp_path / "oauth-state.sqlite3", key,
        {"gmail": "https://app.example.test/oauth/google/callback"},
        clock=clock,
    )
    vault = OAuthCredentialVault(tmp_path / "oauth-credentials.sqlite3", key, clock=clock)
    adapter = GoogleOAuthAdapter(
        client_id="google-client-id",
        client_secret="google-client-secret",
        authorization_service=auth,
        credential_vault=vault,
        enabled=enabled,
        transport=transport or FakeTransport(),
        clock=clock,
    )
    return adapter, auth, vault


def test_adapter_is_disabled_by_default(tmp_path):
    adapter, _, _ = make_adapter(tmp_path, enabled=False)
    with pytest.raises(GoogleOAuthError, match="disabled"):
        adapter.begin("actor-1", "key-1")


def test_begin_builds_google_url_with_state_pkce_and_fixed_redirect(tmp_path):
    adapter, _, _ = make_adapter(tmp_path)
    result = adapter.begin("actor-1", "key-1")
    parsed = urlparse(result["authorization_url"])
    params = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == GOOGLE_AUTHORIZATION_ENDPOINT
    assert params["state"] == [result["state"]]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["redirect_uri"] == ["https://app.example.test/oauth/google/callback"]
    assert "google-client-secret" not in result["authorization_url"]
    assert "code_verifier" not in result


def test_complete_exchanges_code_verifies_identity_and_stores_encrypted_tokens(tmp_path):
    transport = FakeTransport()
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    result = adapter.complete(code="one-time-code", state=started["state"], actor_id="actor-1", key_id="key-1")

    assert result["authorized"] is True
    assert result["account_label"] == "p***@e***"
    assert result["expires_at"] == 4600.0
    assert set(GOOGLE_REQUIRED_SCOPES).issubset(result["scopes"])
    assert "ACCESS_SECRET" not in repr(result) and "REFRESH_SECRET" not in repr(result)
    assert transport.token_calls[0][1]["code"] == "one-time-code"
    assert transport.token_calls[0][1]["code_verifier"]
    assert transport.token_calls[0][1]["client_secret"] == "google-client-secret"
    assert transport.identity_calls[0][1] == "ACCESS_SECRET"
    assert vault.status("gmail", "google-sub-123", "actor-1")["authorized"] is True
    assert vault.get_tokens_for_adapter("gmail", "google-sub-123", "actor-1")["access_token"] == "ACCESS_SECRET"


def test_state_is_bound_to_actor_and_cannot_be_replayed(tmp_path):
    adapter, _, _ = make_adapter(tmp_path)
    started = adapter.begin("actor-1", "key-1")
    with pytest.raises(GoogleOAuthError, match="state is invalid"):
        adapter.complete(code="code", state=started["state"], actor_id="other-actor", key_id="key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    with pytest.raises(GoogleOAuthError, match="state is invalid"):
        adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")


def test_unverified_identity_is_never_stored(tmp_path):
    transport = FakeTransport(identity={"sub": "sub-1", "email": "person@example.com", "email_verified": False})
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    with pytest.raises(GoogleOAuthError, match="could not be verified"):
        adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    assert vault.status("gmail", "sub-1", "actor-1")["authorized"] is False


def test_missing_required_scope_is_rejected_without_saving_credentials(tmp_path):
    transport = FakeTransport(token_response={
        "access_token": "ACCESS_SECRET", "token_type": "Bearer", "expires_in": 3600,
        "scope": "openid email profile",
    })
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    with pytest.raises(GoogleOAuthError, match="did not grant all required scopes"):
        adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    assert vault.status("gmail", "google-sub-123", "actor-1")["authorized"] is False
    assert transport.identity_calls == []


def test_transport_failure_does_not_leak_provider_exception_or_allow_replay(tmp_path):
    transport = FakeTransport(error=RuntimeError("ACCESS_SECRET REFRESH_SECRET"))
    adapter, _, _ = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    with pytest.raises(GoogleOAuthError) as exc:
        adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    assert "ACCESS_SECRET" not in str(exc.value)
    assert "REFRESH_SECRET" not in str(exc.value)
    with pytest.raises(GoogleOAuthError, match="state is invalid"):
        adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")


def test_constructor_requires_required_scopes_and_gmail_redirect(tmp_path):
    key = Fernet.generate_key()
    auth = OAuthAuthorizationService(tmp_path / "state.sqlite3", key, {"gmail": "https://app.example.test/callback"})
    vault = OAuthCredentialVault(tmp_path / "vault.sqlite3", key)
    with pytest.raises(ValueError, match="required identity and Gmail send scopes"):
        GoogleOAuthAdapter(
            client_id="id", client_secret="secret", authorization_service=auth,
            credential_vault=vault, scopes=("openid", "email", "profile"),
        )


def test_google_userinfo_scope_aliases_are_accepted(tmp_path):
    transport = FakeTransport(token_response={
        "access_token": "ACCESS_SECRET",
        "refresh_token": "REFRESH_SECRET",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "openid https://www.googleapis.com/auth/userinfo.email "
                 "https://www.googleapis.com/auth/userinfo.profile "
                 "https://www.googleapis.com/auth/gmail.send",
    })
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    result = adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    assert result["authorized"] is True
    assert vault.status("gmail", "google-sub-123", "actor-1")["authorized"] is True


def test_browser_binding_is_required_and_one_time(tmp_path):
    adapter, _, vault = make_adapter(tmp_path)
    binding = "session-binding-" + "X" * 32
    started = adapter.begin("actor-1", "key-1", browser_session_binding=binding)
    with pytest.raises(GoogleOAuthError, match="browser"):
        adapter.complete_browser(code="code", state=started["state"], browser_session_binding="Y" * 43)
    result = adapter.complete_browser(code="code", state=started["state"], browser_session_binding=binding)
    assert result["authorized"] is True
    with pytest.raises(GoogleOAuthError, match="browser"):
        adapter.complete_browser(code="code", state=started["state"], browser_session_binding=binding)
    assert vault.status("gmail", "google-sub-123", "actor-1")["authorized"] is True


def test_existing_oauth_state_database_migrates_browser_binding_column(tmp_path):
    import sqlite3
    path = tmp_path / "legacy-state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE oauth_authorization_attempts (
                state_hash TEXT PRIMARY KEY, provider_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                key_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, encrypted_code_verifier TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','consumed','expired')), consumed_at REAL
            )
        """)
    key = Fernet.generate_key()
    service = OAuthAuthorizationService(path, key, {"gmail": "https://app.example.test/callback"})
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(oauth_authorization_attempts)")}
    assert "browser_session_hash" in columns
    assert service.start("gmail", "actor", "key", browser_session_binding="Z" * 43)["state"]



def test_refresh_rotates_access_token_and_preserves_existing_refresh_token(tmp_path):
    now = [1000.0]
    transport = FakeTransport()
    adapter, _, vault = make_adapter(tmp_path, transport=transport, clock=lambda: now[0])
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")

    # A valid token is not refreshed unnecessarily.
    not_needed = adapter.refresh_account_tokens("google-sub-123", "actor-1")
    assert not_needed["refresh_performed"] is False
    assert not_needed["account_label"] == "p***@e***"

    now[0] = 5000.0
    refreshed = adapter.refresh_account_tokens("google-sub-123", "actor-1")
    assert refreshed["refresh_performed"] is True
    assert refreshed["expires_at"] == 12200.0
    tokens = vault.get_tokens_for_adapter("gmail", "google-sub-123", "actor-1")
    assert tokens["access_token"] == "ACCESS_REFRESHED"
    assert tokens["refresh_token"] == "REFRESH_SECRET"
    refresh_call = [form for _, form in transport.token_calls if form.get("grant_type") == "refresh_token"]
    assert len(refresh_call) == 1
    assert refresh_call[0]["refresh_token"] == "REFRESH_SECRET"
    assert "ACCESS_REFRESHED" not in repr(refreshed)


def test_refresh_failure_releases_lease_and_does_not_leak_provider_error(tmp_path):
    now = [1000.0]
    transport = FakeTransport()
    adapter, _, vault = make_adapter(tmp_path, transport=transport, clock=lambda: now[0])
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    now[0] = 5000.0
    transport.error = RuntimeError("REFRESH_SECRET ACCESS_REFRESHED")

    with pytest.raises(GoogleOAuthError) as exc:
        adapter.refresh_account_tokens("google-sub-123", "actor-1")
    assert "REFRESH_SECRET" not in str(exc.value)
    assert "ACCESS_REFRESHED" not in str(exc.value)
    status = vault.status("gmail", "google-sub-123", "actor-1")
    assert status["refresh_in_progress"] is False
    assert status["authorized"] is True  # A refresh token remains available, but the expired access token needs attention.


def test_refresh_lease_blocks_concurrent_refresh_and_rejects_stale_completion(tmp_path):
    vault = OAuthCredentialVault(tmp_path / "lease.sqlite3", Fernet.generate_key(), clock=lambda: 1000.0)
    vault.store_tokens("gmail", "account-1", "ACCESS_OLD", "REFRESH_OLD", 900.0,
                       GOOGLE_REQUIRED_SCOPES, identity_verified=True, owner_actor_id="actor-1")
    first = vault.claim_token_refresh("gmail", "account-1", "actor-1")
    assert first["status"] == "claimed"
    with pytest.raises(Exception, match="already in progress"):
        vault.claim_token_refresh("gmail", "account-1", "actor-1")
    assert vault.complete_token_refresh("gmail", "account-1", "actor-1", first["lease_token"],
                                        "ACCESS_NEW", None, 5000.0, GOOGLE_REQUIRED_SCOPES)
    assert vault.complete_token_refresh("gmail", "account-1", "actor-1", first["lease_token"],
                                        "ACCESS_STALE", None, 6000.0, GOOGLE_REQUIRED_SCOPES) is False
    assert vault.get_tokens_for_adapter("gmail", "account-1", "actor-1")["access_token"] == "ACCESS_NEW"


def test_disconnect_revokes_provider_token_and_always_disables_local_credentials(tmp_path):
    transport = FakeTransport()
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")

    result = adapter.disconnect_account("google-sub-123", "actor-1")
    assert result["local_disconnected"] is True
    assert result["provider_revocation"] == "succeeded"
    assert transport.revocation_calls == ["REFRESH_SECRET"]
    assert vault.status("gmail", "google-sub-123", "actor-1")["issue"] == "revoked"
    with pytest.raises(Exception, match="unavailable or revoked"):
        vault.get_tokens_for_adapter("gmail", "google-sub-123", "actor-1")


def test_provider_revocation_failure_still_disables_local_credentials(tmp_path):
    transport = FakeTransport(revoke_error=RuntimeError("provider failure with REFRESH_SECRET"))
    adapter, _, vault = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")

    result = adapter.disconnect_account("google-sub-123", "actor-1")
    assert result["local_disconnected"] is True
    assert result["provider_revocation"] == "failed"
    assert vault.status("gmail", "google-sub-123", "actor-1")["issue"] == "revoked"
    assert "REFRESH_SECRET" not in repr(result)



def test_expired_refresh_lease_can_be_reclaimed_after_worker_interruption(tmp_path):
    now = [1000.0]
    vault = OAuthCredentialVault(tmp_path / "lease-recovery.sqlite3", Fernet.generate_key(), clock=lambda: now[0])
    vault.store_tokens("gmail", "account-1", "ACCESS_OLD", "REFRESH_OLD", 900.0,
                       GOOGLE_REQUIRED_SCOPES, identity_verified=True, owner_actor_id="actor-1")
    abandoned = vault.claim_token_refresh("gmail", "account-1", "actor-1", lease_seconds=5)
    now[0] = 1006.0
    recovered = vault.claim_token_refresh("gmail", "account-1", "actor-1", lease_seconds=5)
    assert recovered["status"] == "claimed"
    assert recovered["lease_token"] != abandoned["lease_token"]
    assert vault.complete_token_refresh("gmail", "account-1", "actor-1", abandoned["lease_token"],
                                        "ACCESS_STALE", None, 5000.0, GOOGLE_REQUIRED_SCOPES) is False
    assert vault.complete_token_refresh("gmail", "account-1", "actor-1", recovered["lease_token"],
                                        "ACCESS_RECOVERED", None, 5000.0, GOOGLE_REQUIRED_SCOPES) is True
    assert vault.get_tokens_for_adapter("gmail", "account-1", "actor-1")["access_token"] == "ACCESS_RECOVERED"


def test_gmail_send_uses_owner_scoped_credentials_and_returns_only_receipt(tmp_path):
    transport = FakeTransport()
    adapter, _, _ = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    result = adapter.send_gmail_message("google-sub-123", "actor-1", "dGVzdA")
    assert result == {"provider_message_id": "gmail-message-123", "status": "sent"}
    assert transport.gmail_calls == [("ACCESS_SECRET", "dGVzdA")]
    assert "ACCESS_SECRET" not in repr(result) and "REFRESH_SECRET" not in repr(result)


def test_gmail_transport_failure_is_marked_ambiguous(tmp_path):
    transport = FakeTransport()
    transport.gmail_error = RuntimeError("secret response body")
    adapter, _, _ = make_adapter(tmp_path, transport=transport)
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    from app.connectors.google_oauth import GoogleGmailOutcomeUncertain
    with pytest.raises(GoogleGmailOutcomeUncertain, match="ambiguous") as exc:
        adapter.send_gmail_message("google-sub-123", "actor-1", "dGVzdA")
    assert "secret response body" not in str(exc.value)
    assert len(transport.gmail_calls) == 1


def test_real_gmail_transport_uses_only_fixed_endpoint_and_bearer_header(monkeypatch):
    import json
    from app.connectors.google_oauth import GOOGLE_GMAIL_SEND_ENDPOINT, UrllibGoogleOAuthTransport
    transport = UrllibGoogleOAuthTransport()
    captured = {}
    def inspect_request(request):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return {"id": "message-1"}
    monkeypatch.setattr(transport, "_request_json", inspect_request)
    assert transport.send_gmail_message("access-secret", "encoded-mime") == {"id": "message-1"}
    assert captured["url"] == GOOGLE_GMAIL_SEND_ENDPOINT
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer access-secret"
    assert captured["body"] == {"raw": "encoded-mime"}


def test_gmail_send_refreshes_expiring_access_token_before_network_call(tmp_path):
    now = [1000.0]
    transport = FakeTransport()
    adapter, _, _ = make_adapter(tmp_path, transport=transport, clock=lambda: now[0])
    started = adapter.begin("actor-1", "key-1")
    adapter.complete(code="code", state=started["state"], actor_id="actor-1", key_id="key-1")
    now[0] = 5000.0
    result = adapter.send_gmail_message("google-sub-123", "actor-1", "dGVzdA")
    assert result["status"] == "sent"
    assert transport.token_calls[-1][1]["grant_type"] == "refresh_token"
    assert transport.gmail_calls[0][0] == "ACCESS_REFRESHED"
