"""v3.66 explicit AI provider connectivity test; provider traffic is mocked."""
import json
from fastapi.testclient import TestClient
import pytest

from app.llm_provider import LLMNotConfigured, LLMProviderError, test_provider_connection as run_provider_connection
from app.main import app

CONFIG = {
    "ALEX_LLM_BASE_URL": "https://llm.example.test/v1",
    "ALEX_LLM_API_KEY": "private-v366-test-secret",
    "ALEX_LLM_MODEL": "test-model-v366",
}

def fake_transport(url, headers, body, timeout):
    assert url == "https://llm.example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer private-v366-test-secret"
    assert timeout == 8.0
    payload = json.loads(body.decode())
    assert payload["max_tokens"] == 12
    assert "ALEX_CONNECTION_OK" in payload["messages"][1]["content"]
    return json.dumps({"choices": [{"message": {"content": "ALEX_CONNECTION_OK"}}]}).encode()

def test_connection_test_returns_safe_success_metadata():
    result = run_provider_connection(env=CONFIG, transport=fake_transport)
    assert result["state"] == "connected"
    assert result["connection_tested"] is True
    assert result["model_label"] == "test-model-v366"
    assert CONFIG["ALEX_LLM_API_KEY"] not in json.dumps(result)
    assert "llm.example.test" not in json.dumps(result)

def test_connection_test_requires_configuration():
    with pytest.raises(LLMNotConfigured):
        run_provider_connection(env={}, transport=fake_transport)

def test_connection_test_sanitizes_invalid_provider_response():
    def invalid(*args): return b"not-json"
    with pytest.raises(LLMProviderError, match="invalid response format"):
        run_provider_connection(env=CONFIG, transport=invalid)

def test_connection_test_sanitizes_transport_failure():
    def fail(*args): raise LLMProviderError("AI provider could not be reached or timed out.")
    with pytest.raises(LLMProviderError, match="could not be reached or timed out"):
        run_provider_connection(env=CONFIG, transport=fail)

def test_connection_test_endpoint_is_admin_only_and_safe(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "admin-v366", "key_id": "admin-v366", "actor_id": "admin-v366", "role": "admin"},
        {"key": "operator-v366", "key_id": "operator-v366", "actor_id": "operator-v366", "role": "operator"},
    ]))
    from app import main as main_module
    monkeypatch.setattr(main_module, "test_provider_connection", lambda: {
        "state": "connected", "connection_tested": True, "model_label": "safe-model",
        "message": "The configured AI provider returned a valid response to the connection test."})
    client = TestClient(app)
    denied = client.post("/api/v1/system/ai-provider-test", headers={"Authorization": "Bearer operator-v366"}, json={})
    assert denied.status_code == 403
    response = client.post("/api/v1/system/ai-provider-test", headers={"Authorization": "Bearer admin-v366"}, json={})
    assert response.status_code == 200, response.text
    assert response.json()["connection_tested"] is True
    assert "private-v366-test-secret" not in response.text
    assert "llm.example.test" not in response.text

def test_connection_test_endpoint_returns_safe_unavailable_status(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "admin-v366-missing", "key_id": "admin-v366-missing", "actor_id": "admin", "role": "admin"},
    ]))
    from app import main as main_module
    monkeypatch.setattr(main_module, "test_provider_connection", lambda: (_ for _ in ()).throw(LLMNotConfigured()))
    response = TestClient(app).post("/api/v1/system/ai-provider-test", headers={"Authorization": "Bearer admin-v366-missing"}, json={})
    assert response.status_code == 503
    assert "secret" not in response.text.lower()

def test_dashboard_exposes_admin_connection_test_control():
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/dashboard-assets/dashboard.js").text
    assert 'id="ai-provider-test"' in html
    assert "/api/v1/system/ai-provider-test" in js
    assert "No report notes were sent" in js
    assert client.get("/health").json()["version"] == "3.66"
