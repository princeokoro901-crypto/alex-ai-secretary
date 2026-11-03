"""v3.66 end-to-end contract for the first useful local workspace task."""
import json

from fastapi.testclient import TestClient

from app.main import app


SAMPLE_NOTES = """The prototype dashboard is running locally.
The role permission tests pass.
Next step: validate direct browser networking in a normal desktop environment."""


def test_operator_can_create_a_verified_local_report_without_external_actions(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-v363", "key_id": "operator-v363", "actor_id": "operator-test", "role": "operator"}
    ]))
    client = TestClient(app)
    response = client.post(
        "/api/v1/workspace/draft-report",
        headers={"Authorization": "Bearer operator-v363"},
        json={"title": "Prototype Progress", "audience": "Project team", "notes": SAMPLE_NOTES},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["task_type"] == "draft_report_from_notes"
    assert body["execution_mode"] == "local_template"
    assert body["ai_model_used"] is False
    assert body["external_actions_performed"] is False
    assert body["verification"]["passed"] is True
    assert all(body["verification"]["checks"].values())
    assert body["output_sha256"]
    assert "The prototype dashboard is running locally." in body["output_markdown"]
    assert "The role permission tests pass." in body["output_markdown"]
    assert "has not been independently fact-checked" in body["output_markdown"]


def test_reader_cannot_create_report(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "reader-v363", "key_id": "reader-v363", "actor_id": "reader-test", "role": "reader"}
    ]))
    response = TestClient(app).post(
        "/api/v1/workspace/draft-report",
        headers={"Authorization": "Bearer reader-v363"},
        json={"title": "Blocked", "notes": "Some notes"},
    )
    assert response.status_code == 403


def test_report_input_validation_fails_closed(monkeypatch):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    client = TestClient(app)
    assert client.post("/api/v1/workspace/draft-report", json={"title": "Missing notes"}).status_code == 400
    assert client.post("/api/v1/workspace/draft-report", json={"title": "X", "notes": "   "}).status_code == 400
    assert client.post("/api/v1/workspace/draft-report", json={"title": "X", "notes": "a" * 12001}).status_code == 400


def test_dashboard_exposes_report_workflow_and_safe_preview_download():
    client = TestClient(app)
    html = client.get("/").text
    js = client.get("/dashboard-assets/dashboard.js").text
    assert 'id="report-task-form"' in html
    assert 'id="report-notes"' in html
    assert 'id="report-download"' in html
    assert '/api/v1/workspace/draft-report' in js
    assert 'reportTaskResult.textContent = latestReportMarkdown' in js
    assert 'innerHTML' not in js


def test_health_reports_v363(monkeypatch):
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    assert TestClient(app).get("/health").json()["version"] == "3.66"
