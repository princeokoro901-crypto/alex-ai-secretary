import pytest
from app.connectors.sandbox_email import SandboxEmailOutbox


def test_sandbox_email_records_but_never_sends(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    outbox = SandboxEmailOutbox(path)
    result = outbox.queue_simulated({"to": "person@example.com", "subject": "Test", "body": "Hello"})
    assert result["status"] == "simulated_queued"
    assert result["mode"] == "simulation_only"
    assert result["sent"] is False
    assert outbox.list_messages()[0]["message_id"] == result["message_id"]
    outbox.close()
    reopened = SandboxEmailOutbox(path)
    assert reopened.list_messages()[0]["body"] == "Hello"
    reopened.close()


@pytest.mark.parametrize("payload", [
    {"to": "not-an-email", "subject": "Test", "body": "Hello"},
    {"to": "person@example.com", "subject": " ", "body": "Hello"},
    {"to": "person@example.com", "subject": "Test", "body": " "},
])
def test_sandbox_email_rejects_invalid_messages(payload):
    outbox = SandboxEmailOutbox()
    with pytest.raises(ValueError):
        outbox.queue_simulated(payload)
    assert outbox.list_messages() == []
    outbox.close()


def test_sandbox_email_rejects_oversized_subject_and_invalid_limit():
    outbox = SandboxEmailOutbox()
    with pytest.raises(ValueError, match="300 characters"):
        outbox.queue_simulated({"to": "a@example.com", "subject": "s" * 301, "body": "Hello"})
    with pytest.raises(ValueError, match="between 1 and 500"):
        outbox.list_messages(0)
    outbox.close()


def test_end_to_end_sandbox_email_api_requires_approval_and_never_sends(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app.main import app

    entries = [
        {"key": "operator-v320", "key_id": "operator-v320-key", "actor_id": "operator-v320", "role": "operator"},
        {"key": "admin-v320", "key_id": "admin-v320-key", "actor_id": "admin-v320", "role": "admin"},
    ]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    operator = {"Authorization": "Bearer operator-v320"}
    admin = {"Authorization": "Bearer admin-v320"}
    mission_id = "sandbox-email-v320-integration"
    payload = {"to": "demo@example.com", "subject": "Test report", "body": "This is a simulation."}

    mission = client.post("/api/v1/missions", headers=operator, json={
        "mission_id": mission_id, "objective": "Test sandbox email workflow",
        "allowed_tools": ["sandbox.email"], "budget": 0,
    })
    assert mission.status_code == 200
    preview = client.post("/api/v1/connectors/sandbox.email/action-preview", headers=operator, json=payload)
    assert preview.status_code == 200
    action = preview.json()["action"]

    proposed = client.post(f"/api/v1/missions/{mission_id}/actions", headers=operator, json={"action": action})
    assert proposed.status_code == 200
    approval_id = proposed.json()["approval"]["approval_id"]
    decided = client.post(f"/api/v1/approvals/{approval_id}/decision", headers=admin,
                          json={"decision": "approved", "reason": "Approved simulation test"})
    assert decided.status_code == 200
    issued = client.post(f"/api/v1/approvals/{approval_id}/execution-permit", headers=operator,
                         json={"mission_id": mission_id, "action": action})
    assert issued.status_code == 200

    # The same permit cannot be used to change the recipient or message body.
    tampered = {"permit_id": issued.json()["execution_permit_id"], "mission_id": mission_id,
                "action": action, "input": {**payload, "to": "different@example.com"}}
    denied = client.post("/api/v1/connectors/sandbox.email/execute", headers=operator, json=tampered)
    assert denied.status_code == 400

    valid = {"permit_id": issued.json()["execution_permit_id"], "mission_id": mission_id,
             "action": action, "input": payload}
    executed = client.post("/api/v1/connectors/sandbox.email/execute", headers=operator, json=valid)
    assert executed.status_code == 200
    result = executed.json()["execution"]
    assert result["status"] == "succeeded"
    assert result["result"]["sent"] is False
    assert result["result"]["mode"] == "simulation_only"

    outbox = client.get("/api/v1/sandbox-email/outbox", headers=admin)
    assert outbox.status_code == 200
    assert any(message["message_id"] == result["result"]["message_id"] for message in outbox.json()["messages"])
    assert client.get("/api/v1/sandbox-email/outbox", headers=operator).status_code == 403


def test_idempotency_key_replays_same_message_without_duplicate(tmp_path):
    outbox = SandboxEmailOutbox(str(tmp_path / "idempotent.sqlite3"))
    payload = {"to": "person@example.com", "subject": "Report", "body": "Hello", "idempotency_key": "mission-123-email"}
    first = outbox.queue_simulated(payload)
    second = outbox.queue_simulated(payload)
    assert first["duplicate_replay"] is False
    assert second["duplicate_replay"] is True
    assert first["message_id"] == second["message_id"]
    assert len(outbox.list_messages()) == 1
    outbox.close()


def test_idempotency_key_cannot_be_reused_for_different_content():
    outbox = SandboxEmailOutbox()
    outbox.queue_simulated({"to": "person@example.com", "subject": "Report", "body": "Hello", "idempotency_key": "job-77"})
    with pytest.raises(ValueError, match="different email payload"):
        outbox.queue_simulated({"to": "other@example.com", "subject": "Report", "body": "Hello", "idempotency_key": "job-77"})
    assert len(outbox.list_messages()) == 1
    outbox.close()


def test_idempotency_key_is_validated_and_bound_to_approval_action():
    from app.connectors.sandbox_email import sandbox_email_action
    base = {"to": "person@example.com", "subject": "Report", "body": "Hello"}
    first = sandbox_email_action({**base, "idempotency_key": "job-1"})
    second = sandbox_email_action({**base, "idempotency_key": "job-2"})
    assert first != second
    outbox = SandboxEmailOutbox()
    with pytest.raises(ValueError, match="idempotency_key"):
        outbox.queue_simulated({**base, "idempotency_key": "bad key"})
    outbox.close()


def test_v320_outbox_schema_is_migrated_without_losing_existing_messages(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.sqlite3")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sandbox_email_outbox (message_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL CHECK(status = 'simulated_queued'), created_at TEXT NOT NULL)")
    db.execute("INSERT INTO sandbox_email_outbox VALUES ('old-message', 'person@example.com', 'Old', 'Existing', 'simulated_queued', '2026-01-01T00:00:00+00:00')")
    db.commit(); db.close()
    outbox = SandboxEmailOutbox(path)
    messages = outbox.list_messages()
    assert len(messages) == 1
    assert messages[0]["message_id"] == "old-message"
    assert messages[0]["idempotency_key"] is None
    outbox.close()
