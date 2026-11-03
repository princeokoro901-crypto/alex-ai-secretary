"""v3.66 AI-provider adapter tests; all provider responses are mocked locally."""
import json
import pytest
from fastapi.testclient import TestClient

from app.ai_workspace import draft_ai_report
from app.llm_provider import LLMNotConfigured, LLMProviderError, chat_completion
from app.main import app

CONFIG = {
    "ALEX_LLM_BASE_URL": "https://llm.example.test/v1",
    "ALEX_LLM_API_KEY": "test-secret-never-return-this",
    "ALEX_LLM_MODEL": "test-model",
}


def fake_transport(url, headers, body, timeout):
    assert url == "https://llm.example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret-never-return-this"
    assert timeout == 25.0
    payload = json.loads(body.decode())
    assert payload["model"] == "test-model"
    assert payload["messages"][0]["role"] in {"system", "user"}
    return json.dumps({"model": "test-model", "choices": [{"message": {"content": "## Summary\n\nThe project is progressing.\n\n## Recommended next steps\n\n- Verify the browser workflow."}}]}).encode()


def test_chat_completion_uses_configured_provider_without_leaking_secret():
    content, model = chat_completion([{"role": "user", "content": "hello"}], env=CONFIG, transport=fake_transport)
    assert "project is progressing" in content
    assert model == "test-model"
    assert CONFIG["ALEX_LLM_API_KEY"] not in content


def test_provider_fails_closed_when_not_configured():
    with pytest.raises(LLMNotConfigured):
        chat_completion([{"role": "user", "content": "hello"}], env={})


def test_provider_rejects_plain_http_non_loopback():
    unsafe = {**CONFIG, "ALEX_LLM_BASE_URL": "http://llm.example.test/v1"}
    with pytest.raises(LLMNotConfigured):
        chat_completion([{"role": "user", "content": "hello"}], env=unsafe, transport=fake_transport)


def test_provider_rejects_invalid_response_shape():
    def invalid(*args):
        return b'{"unexpected": true}'
    with pytest.raises(LLMProviderError):
        chat_completion([{"role": "user", "content": "hello"}], env=CONFIG, transport=invalid)


def test_ai_report_is_marked_for_review_and_verifies_output():
    result = draft_ai_report({"title": "Project update", "audience": "Team", "notes": "Progress is ongoing."}, env=CONFIG, transport=fake_transport)
    assert result["status"] == "completed"
    assert result["execution_mode"] == "configured_ai_model"
    assert result["ai_model_used"] is True
    assert result["external_actions_performed"] is False
    assert result["verification"]["passed"] is True
    assert "AI-generated draft" in result["output_markdown"]
    assert "Project update" in result["output_markdown"]
    assert CONFIG["ALEX_LLM_API_KEY"] not in json.dumps(result)


def test_ai_report_endpoint_returns_503_when_provider_is_unconfigured(monkeypatch):
    monkeypatch.delenv("ALEX_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ALEX_LLM_API_KEY", raising=False)
    monkeypatch.delenv("ALEX_LLM_MODEL", raising=False)
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-v364", "key_id": "operator-v364", "actor_id": "operator-test", "role": "operator"}
    ]))
    response = TestClient(app).post("/api/v1/workspace/ai-draft-report", headers={"Authorization": "Bearer operator-v364"},
                                   json={"title": "Project update", "notes": "Notes."})
    assert response.status_code == 503
    assert "test-secret-never-return-this" not in response.text


def test_reader_cannot_use_ai_report_endpoint(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "reader-v364", "key_id": "reader-v364", "actor_id": "reader-test", "role": "reader"}
    ]))
    response = TestClient(app).post("/api/v1/workspace/ai-draft-report", headers={"Authorization": "Bearer reader-v364"},
                                   json={"title": "Project update", "notes": "Notes."})
    assert response.status_code == 403


def test_dashboard_exposes_explicit_ai_opt_in_and_new_health_version():
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/dashboard-assets/dashboard.js").text
    assert 'id="report-use-ai"' in html
    assert "/api/v1/workspace/ai-draft-report" in js
    assert client.get("/health").json()["version"] == "3.66"


def test_ai_report_endpoint_returns_ai_labeled_result_when_adapter_succeeds(monkeypatch):
    from app import main as main_module
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-ai-ok", "key_id": "operator-ai-ok", "actor_id": "operator-test", "role": "operator"}
    ]))
    fake_result = {"task_id": "ai-draft-test", "status": "completed", "source_line_count": 1,
                   "output_sha256": "abc123", "model_label": "test-model", "ai_model_used": True,
                   "external_actions_performed": False, "output_markdown": "# Test\nAI-generated draft",
                   "verification": {"passed": True, "checks": {"output_nonempty": True}}}
    monkeypatch.setattr(main_module, "draft_ai_report", lambda payload: fake_result)
    response = TestClient(app).post("/api/v1/workspace/ai-draft-report", headers={"Authorization": "Bearer operator-ai-ok"},
                                   json={"title": "Test", "notes": "Notes."})
    assert response.status_code == 200, response.text
    assert response.json()["ai_model_used"] is True
    assert response.json()["external_actions_performed"] is False


def test_ai_report_endpoint_sanitizes_provider_errors(monkeypatch):
    from app import main as main_module
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-ai-error", "key_id": "operator-ai-error", "actor_id": "operator-test", "role": "operator"}
    ]))
    monkeypatch.setattr(main_module, "draft_ai_report", lambda payload: (_ for _ in ()).throw(LLMProviderError("AI provider returned HTTP 401.")))
    response = TestClient(app).post("/api/v1/workspace/ai-draft-report", headers={"Authorization": "Bearer operator-ai-error"},
                                   json={"title": "Test", "notes": "Notes."})
    assert response.status_code == 502
    assert "API key" not in response.text
    assert "secret" not in response.text.lower()
