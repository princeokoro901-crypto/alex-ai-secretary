import json
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.readiness import build_readiness_report
from app.main import app


class FakeAudit:
    path = None
    def verify(self):
        return True


class FakeOAuth:
    enabled = False
    authorization_service = None
    credential_vault = None


class FakeGmail:
    ledger = None
    def authorization_status(self):
        return {"authorized": False}


def _report(tmp_path, *, api_keys=None, oauth=None, gmail=None, real=False, allow=(),
            audit=None, journal=None, approvals=None, orchestration=None):
    return build_readiness_report(
        api_keys=api_keys, api_key_config_error=False,
        orchestration_state=orchestration or SimpleNamespace(path=str(tmp_path / "orchestration.json")),
        audit_log=audit or SimpleNamespace(path=str(tmp_path / "audit.jsonl"), verify=lambda: True),
        approval_service=approvals or SimpleNamespace(path=str(tmp_path / "approvals.json")),
        execution_journal=journal or SimpleNamespace(path=str(tmp_path / "journal.sqlite")),
        google_oauth_adapter=oauth, gmail_email_provider=gmail,
        real_email_enabled=real, allowed_email_providers=allow,
    )


def test_readiness_report_does_not_claim_ready_when_gmail_is_disabled(tmp_path):
    report = _report(tmp_path)
    assert report["ready_for_live_gmail"] is False
    assert report["application_mode"] == "live_gmail_blocked"
    assert "gmail_sender_enabled" in report["blockers"]
    assert "api_admin_authentication" in report["blockers"]
    assert "path" not in json.dumps(report).lower()


def test_readiness_report_requires_persistent_oauth_and_ledger_stores(tmp_path):
    oauth = FakeOAuth()
    oauth.enabled = True
    oauth.authorization_service = SimpleNamespace(path=str(tmp_path / "state.sqlite"))
    oauth.credential_vault = SimpleNamespace(path=str(tmp_path / "vault.sqlite"))
    gmail = FakeGmail()
    gmail.ledger = SimpleNamespace(path=str(tmp_path / "ledger.sqlite"))
    keys = [{"key": "not-returned", "key_id": "admin-key", "actor_id": "admin", "role": "admin", "active": True}]
    report = _report(tmp_path, api_keys=keys, oauth=oauth, gmail=gmail, real=True, allow=("gmail",))
    assert report["ready_for_live_gmail"] is False  # account authorization is not confirmed
    assert "gmail_account_authorization" in report["blockers"]
    serialized = json.dumps(report)
    assert "not-returned" not in serialized
    assert str(tmp_path) not in serialized


def test_readiness_report_marks_all_requirements_pass_only_when_satisfied(tmp_path):
    # Create files so the access checks exercise the existing-file path.
    paths = {name: tmp_path / name for name in ("orchestration.json", "audit.jsonl", "approvals.json", "journal.sqlite", "state.sqlite", "vault.sqlite", "ledger.sqlite")}
    for path in paths.values():
        path.write_text("", encoding="utf-8")
    oauth = FakeOAuth()
    oauth.enabled = True
    oauth.authorization_service = SimpleNamespace(path=str(paths["state.sqlite"]))
    oauth.credential_vault = SimpleNamespace(path=str(paths["vault.sqlite"]))
    gmail = FakeGmail()
    gmail.ledger = SimpleNamespace(path=str(paths["ledger.sqlite"]))
    gmail.authorization_status = lambda: {"authorized": True}
    keys = [{"key": "secret-value", "key_id": "admin-key", "actor_id": "admin", "role": "admin", "active": True}]
    report = build_readiness_report(
        api_keys=keys, api_key_config_error=False,
        orchestration_state=SimpleNamespace(path=str(paths["orchestration.json"])),
        audit_log=SimpleNamespace(path=str(paths["audit.jsonl"]), verify=lambda: True),
        approval_service=SimpleNamespace(path=str(paths["approvals.json"])),
        execution_journal=SimpleNamespace(path=str(paths["journal.sqlite"])),
        google_oauth_adapter=oauth, gmail_email_provider=gmail,
        real_email_enabled=True, allowed_email_providers=("gmail",),
    )
    assert report["ready_for_live_gmail"] is True
    assert report["blockers"] == []
    assert "secret-value" not in json.dumps(report)


def test_readiness_api_is_admin_only_and_redacts_configuration(monkeypatch):
    entries = [
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator", "role": "operator"},
        {"key": "admin-secret", "key_id": "admin-key", "actor_id": "admin", "role": "admin"},
    ]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    denied = client.get("/api/v1/system/readiness", headers={"Authorization": "Bearer operator-secret"})
    assert denied.status_code == 403
    response = client.get("/api/v1/system/readiness", headers={"Authorization": "Bearer admin-secret"})
    assert response.status_code == 200
    body = json.dumps(response.json())
    assert "admin-secret" not in body
    assert "operator-secret" not in body
    assert "ALEX_GOOGLE_OAUTH_CLIENT_SECRET" not in body


def test_readiness_api_fails_closed_for_invalid_api_key_configuration(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", "not-json")
    response = TestClient(app).get("/api/v1/system/readiness")
    assert response.status_code == 503
