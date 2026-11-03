"""v3.65 provider status tests; status inspection never calls a network provider."""
import json
from fastapi.testclient import TestClient

from app.llm_provider import provider_status
from app.main import app

CONFIG = {
    "ALEX_LLM_BASE_URL": "https://llm.example.test/v1",
    "ALEX_LLM_API_KEY": "private-test-secret-do-not-return",
    "ALEX_LLM_MODEL": "example-model",
}


def test_provider_status_reports_missing_configuration_without_details():
    result = provider_status(env={})
    assert result == {
        "state": "not_configured", "configured": False,
        "connection_tested": False, "model_label": None,
        "message": "AI provider is not fully configured on the server.",
    }


def test_provider_status_reports_invalid_configuration_without_url_or_secret():
    result = provider_status(env={**CONFIG, "ALEX_LLM_BASE_URL": "http://public.example.test/v1"})
    assert result["state"] == "invalid_configuration"
    assert result["configured"] is False
    assert "private-test-secret-do-not-return" not in json.dumps(result)
    assert "public.example.test" not in json.dumps(result)


def test_provider_status_handles_malformed_url_without_raising():
    result = provider_status(env={**CONFIG, "ALEX_LLM_BASE_URL": "https://[invalid/v1"})
    assert result["state"] == "invalid_configuration"
    assert result["configured"] is False


def test_provider_status_marks_valid_settings_as_not_live_tested():
    result = provider_status(env=CONFIG)
    assert result["state"] == "configured_not_tested"
    assert result["configured"] is True
    assert result["connection_tested"] is False
    assert result["model_label"] == "example-model"
    assert "private-test-secret-do-not-return" not in json.dumps(result)
    assert "llm.example.test" not in json.dumps(result)


def test_provider_status_endpoint_returns_safe_configuration_state(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-v365", "key_id": "operator-v365", "actor_id": "operator", "role": "operator"}
    ]))
    monkeypatch.setenv("ALEX_LLM_BASE_URL", CONFIG["ALEX_LLM_BASE_URL"])
    monkeypatch.setenv("ALEX_LLM_API_KEY", CONFIG["ALEX_LLM_API_KEY"])
    monkeypatch.setenv("ALEX_LLM_MODEL", CONFIG["ALEX_LLM_MODEL"])
    response = TestClient(app).get("/api/v1/system/ai-provider-status", headers={"Authorization": "Bearer operator-v365"})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "configured_not_tested"
    assert CONFIG["ALEX_LLM_API_KEY"] not in response.text
    assert CONFIG["ALEX_LLM_BASE_URL"] not in response.text


def test_reader_can_check_provider_status_but_cannot_change_configuration(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "reader-v365", "key_id": "reader-v365", "actor_id": "reader", "role": "reader"}
    ]))
    response = TestClient(app).get("/api/v1/system/ai-provider-status", headers={"Authorization": "Bearer reader-v365"})
    assert response.status_code == 200, response.text


def test_dashboard_explains_provider_status_and_fails_closed(monkeypatch):
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/dashboard-assets/dashboard.js").text
    assert 'id="ai-provider-status"' in html
    assert "/api/v1/system/ai-provider-status" in js
    assert 'aiCheckbox.disabled = true' in js
    assert client.get("/health").json()["version"] == "3.66"


def test_missing_authentication_is_rejected_when_keys_are_configured(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "admin-v365", "key_id": "admin-v365", "actor_id": "admin", "role": "admin"}
    ]))
    response = TestClient(app).get("/api/v1/system/ai-provider-status")
    assert response.status_code == 401
