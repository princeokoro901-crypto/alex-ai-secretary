from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.main as main
from app.connectors.google_oauth import GoogleOAuthAdapter, GoogleOAuthError
from app.connectors.oauth_security import OAuthAuthorizationService, OAuthCredentialVault
from app.security import reset_rate_limiter_for_tests
from app.connectors.google_oauth import GOOGLE_REQUIRED_SCOPES


class FakeTransport:
    def __init__(self):
        self.token_calls = []
        self.identity_calls = []
        self.revocation_calls = []
        self.refresh_response = {"access_token": "ACCESS_REFRESHED", "token_type": "Bearer", "expires_in": 7200}

    def post_form(self, url, form):
        self.token_calls.append((url, dict(form)))
        if form.get("grant_type") == "refresh_token":
            return self.refresh_response
        return {"access_token": "ACCESS_SECRET", "refresh_token": "REFRESH_SECRET",
                "token_type": "Bearer", "expires_in": 3600,
                "scope": " ".join(GOOGLE_REQUIRED_SCOPES)}

    def get_bearer_json(self, url, access_token):
        self.identity_calls.append((url, access_token))
        return {"sub": "google-sub-123", "email": "prince@example.com", "email_verified": True}

    def revoke_token(self, token):
        self.revocation_calls.append(token)


def make_browser_adapter(tmp_path, transport=None, clock=None):
    key = Fernet.generate_key()
    clock_kwargs = {"clock": clock} if clock is not None else {}
    auth = OAuthAuthorizationService(
        tmp_path / "browser-state.sqlite3", key,
        {"gmail": "http://localhost:8000/oauth/google/callback"}, **clock_kwargs,
    )
    vault = OAuthCredentialVault(tmp_path / "browser-vault.sqlite3", key, **clock_kwargs)
    adapter_kwargs = {"clock": clock} if clock is not None else {}
    adapter = GoogleOAuthAdapter(
        client_id="test-client-id", client_secret="test-client-secret",
        authorization_service=auth, credential_vault=vault,
        enabled=True, transport=transport or FakeTransport(), **adapter_kwargs,
    )
    return adapter, vault


def test_browser_bound_callback_completes_and_does_not_reflect_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    adapter, vault = make_browser_adapter(tmp_path)
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)

    started = client.post("/api/v1/email/google/oauth/start")
    assert started.status_code == 200
    cookie = started.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "max-age=600" in cookie
    payload = started.json()
    parsed = parse_qs(urlparse(payload["authorization_url"]).query)
    state = parsed["state"][0]

    callback = client.get(
        "/oauth/google/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/oauth/google/complete"
    assert state not in callback.headers["location"]
    assert "fake-code" not in callback.headers["location"]

    landing = client.get(callback.headers["location"])
    assert landing.status_code == 200
    assert "does not confirm authorization success or email delivery" in landing.text
    assert landing.headers["cache-control"] == "no-store"
    assert landing.headers["referrer-policy"] == "no-referrer"
    assert vault.status("gmail", "google-sub-123", "local:owner")["authorized"] is True
    assert "ACCESS_SECRET" not in landing.text
    reset_rate_limiter_for_tests()


def test_wrong_browser_cookie_cannot_complete_oauth(monkeypatch, tmp_path):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    transport = FakeTransport()
    adapter, vault = make_browser_adapter(tmp_path, transport)
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)
    started = client.post("/api/v1/email/google/oauth/start")
    parsed = parse_qs(urlparse(started.json()["authorization_url"]).query)
    state = parsed["state"][0]

    client.cookies.set("alex_oauth_binding", "A" * 43)
    callback = client.get(
        "/oauth/google/callback", params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert transport.token_calls == []
    assert vault.status("gmail", "google-sub-123", "local:owner")["authorized"] is False
    reset_rate_limiter_for_tests()


def test_duplicate_callback_query_fields_are_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    transport = FakeTransport()
    adapter, vault = make_browser_adapter(tmp_path, transport)
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)
    started = client.post("/api/v1/email/google/oauth/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    callback = client.get(
        "/oauth/google/callback?state=" + state + "&state=" + state + "&code=fake-code",
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert transport.token_calls == []
    assert vault.status("gmail", "google-sub-123", "local:owner")["authorized"] is False
    reset_rate_limiter_for_tests()


def test_provider_denial_consumes_browser_state_and_prevents_replay(tmp_path):
    adapter, _ = make_browser_adapter(tmp_path)
    binding = "B" * 43
    started = adapter.begin("actor-1", "key-1", browser_session_binding=binding)
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]
    context = adapter.cancel_browser(state=state, browser_session_binding=binding)
    assert context["actor_id"] == "actor-1"
    import pytest
    with pytest.raises(GoogleOAuthError, match="state is invalid"):
        adapter.complete_browser(code="code", state=state, browser_session_binding=binding)


def test_google_oauth_start_requires_admin_when_api_keys_are_configured(monkeypatch, tmp_path):
    import json
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator-1", "role": "operator"}
    ]))
    reset_rate_limiter_for_tests()
    adapter, _ = make_browser_adapter(tmp_path)
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)
    response = client.post("/api/v1/email/google/oauth/start", headers={"Authorization": "Bearer operator-secret"})
    assert response.status_code == 403
    reset_rate_limiter_for_tests()



def test_connected_account_inventory_is_redacted_and_owner_scoped(monkeypatch, tmp_path):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    adapter, _ = make_browser_adapter(tmp_path)
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)
    started = client.post("/api/v1/email/google/oauth/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    callback = client.get("/oauth/google/callback", params={"code": "fake-code", "state": state}, follow_redirects=False)
    assert callback.status_code == 303

    response = client.get("/api/v1/email/google/accounts")
    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["account_id"] == "google-sub-123"
    assert account["account_label"] == "p***@e***"
    assert "ACCESS_SECRET" not in response.text and "REFRESH_SECRET" not in response.text
    assert response.json()["tokens_exposed"] is False

    # A different actor sees no linked accounts.
    assert adapter.list_accounts("another-actor") == []
    reset_rate_limiter_for_tests()


def test_account_management_endpoints_are_admin_only(monkeypatch):
    import json
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator-1", "role": "operator"}
    ]))
    reset_rate_limiter_for_tests()
    client = TestClient(main.app)
    assert client.get("/api/v1/email/google/accounts", headers={"Authorization": "Bearer operator-secret"}).status_code == 403
    assert client.post("/api/v1/email/google/accounts/account-1/refresh", headers={"Authorization": "Bearer operator-secret"}).status_code == 403
    assert client.post("/api/v1/email/google/accounts/account-1/disconnect", headers={"Authorization": "Bearer operator-secret"}).status_code == 403
    reset_rate_limiter_for_tests()



def test_account_refresh_and_disconnect_routes_use_owner_identity_and_redact_tokens(monkeypatch, tmp_path):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    now = [1000.0]
    transport = FakeTransport()
    adapter, vault = make_browser_adapter(tmp_path, transport=transport, clock=lambda: now[0])
    monkeypatch.setattr(main, "google_oauth_adapter", adapter)
    client = TestClient(main.app)
    started = client.post("/api/v1/email/google/oauth/start")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    callback = client.get("/oauth/google/callback", params={"code": "fake-code", "state": state}, follow_redirects=False)
    assert callback.status_code == 303

    now[0] = 5000.0
    refreshed = client.post("/api/v1/email/google/accounts/google-sub-123/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_performed"] is True
    assert "ACCESS_REFRESHED" not in refreshed.text and "REFRESH_SECRET" not in refreshed.text
    assert vault.get_tokens_for_adapter("gmail", "google-sub-123", "local:owner")["access_token"] == "ACCESS_REFRESHED"

    disconnected = client.post("/api/v1/email/google/accounts/google-sub-123/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["local_disconnected"] is True
    assert disconnected.json()["provider_revocation"] == "succeeded"
    assert "REFRESH_SECRET" not in disconnected.text
    assert vault.status("gmail", "google-sub-123", "local:owner")["issue"] == "revoked"
    reset_rate_limiter_for_tests()


def test_gmail_action_preview_is_disabled_without_explicit_sender_configuration(monkeypatch):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    reset_rate_limiter_for_tests()
    client = TestClient(main.app)
    response = client.post("/api/v1/connectors/gmail.email/action-preview", json={
        "to": "recipient@example.com", "subject": "Test", "body": "No send", "idempotency_key": "test-1"
    })
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    reset_rate_limiter_for_tests()


def test_gmail_action_preview_is_admin_only(monkeypatch):
    import json
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator-1", "role": "operator"}
    ]))
    reset_rate_limiter_for_tests()
    client = TestClient(main.app)
    response = client.post("/api/v1/connectors/gmail.email/action-preview", json={
        "to": "recipient@example.com", "subject": "Test", "body": "No send", "idempotency_key": "test-1"
    }, headers={"Authorization": "Bearer operator-secret"})
    assert response.status_code == 403
    reset_rate_limiter_for_tests()
