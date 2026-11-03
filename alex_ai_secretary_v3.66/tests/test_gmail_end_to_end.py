"""API-level Gmail workflow tests with a fake Google transport only."""
import json

from fastapi.testclient import TestClient

from app import main
from app.approvals import ApprovalService
from app.audit import AuditLog
from app.connectors.registry import ConnectorManifest, ConnectorRegistry
from app.execution import ExecutionGateway
from app.execution_journal import ExecutionJournal
from app.missions.models import MissionService
from testsupport_gmail import make_connected_sender

ADMIN_KEY = "local-e2e-admin-key"
ADMIN_ACTOR = {"actor_id": "admin-test", "key_id": "admin-e2e", "role": "admin"}
OPERATOR_KEY = "local-e2e-operator-key"
PAYLOAD = {
    "to": "recipient@example.com",
    "subject": "Alex simulated end-to-end test",
    "body": "This message is handled only by a fake Google transport.",
    "idempotency_key": "alex-e2e-key-001",
}


def _wire_fake_gmail(monkeypatch, tmp_path):
    _, transport, provider, provider_registry = make_connected_sender(tmp_path)
    approvals = ApprovalService(str(tmp_path / "approvals.json"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    journal = ExecutionJournal(str(tmp_path / "execution-journal.sqlite3"))
    gateway = ExecutionGateway(approvals, audit.record, journal)
    connectors = ConnectorRegistry(gateway)
    connectors.register(
        ConnectorManifest(
            connector_id="gmail.email",
            name="Gmail Email (test only)",
            description="Fake-transport integration test.",
            action_type="email.send_gmail",
            input_fields={"to": "string", "subject": "string", "body": "string", "idempotency_key": "string"},
            required_fields=("to", "subject", "body", "idempotency_key"),
            allowed_roles=("admin",),
        ),
        lambda payload, action, context: provider_registry.send(
            "gmail", payload, action, execution_context=context
        ),
        action_builder=lambda payload: provider_registry.build_action("gmail", payload),
        action_aware_handler=True,
        execution_context_handler=True,
    )

    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": ADMIN_KEY, "key_id": ADMIN_ACTOR["key_id"],
         "actor_id": ADMIN_ACTOR["actor_id"], "role": "admin", "active": True},
        {"key": OPERATOR_KEY, "key_id": "operator-e2e", "actor_id": "operator-test",
         "role": "operator", "active": True},
    ]))
    monkeypatch.setattr(main, "gmail_email_provider", provider)
    monkeypatch.setattr(main, "email_provider_registry", provider_registry)
    monkeypatch.setattr(main, "approval_service", approvals)
    monkeypatch.setattr(main, "audit_log", audit)
    monkeypatch.setattr(main, "execution_journal", journal)
    monkeypatch.setattr(main, "execution_gateway", gateway)
    monkeypatch.setattr(main, "connector_registry", connectors)
    monkeypatch.setattr(main, "mission_service", MissionService(workforce=main.workforce))
    return TestClient(main.app), transport, provider, journal


def _approved_send(client, payload, mission_id):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    preview = client.post("/api/v1/connectors/gmail.email/action-preview", json=payload, headers=headers)
    assert preview.status_code == 200, preview.text
    action = preview.json()["action"]

    operator_headers = {"Authorization": f"Bearer {OPERATOR_KEY}"}
    created = client.post("/api/v1/missions", json={
        "mission_id": mission_id,
        "objective": "Exercise approved Gmail workflow with a fake transport",
        "scope": ["simulated test only"],
        "allowed_tools": ["gmail.email"],
        "forbidden_actions": ["send without approval"],
        "budget": 0,
    }, headers=operator_headers)
    assert created.status_code == 200, created.text

    proposed = client.post(f"/api/v1/missions/{mission_id}/actions", json={"action": action}, headers=operator_headers)
    assert proposed.status_code == 200, proposed.text
    approval_id = proposed.json()["approval"]["approval_id"]

    decision = client.post(f"/api/v1/approvals/{approval_id}/decision", json={
        "decision": "approved", "reason": "Approved for isolated fake-transport integration test"
    }, headers=headers)
    assert decision.status_code == 200, decision.text

    permit = client.post(f"/api/v1/approvals/{approval_id}/execution-permit", json={
        "mission_id": mission_id, "action": action
    }, headers=headers)
    assert permit.status_code == 200, permit.text

    executed = client.post("/api/v1/connectors/gmail.email/execute", json={
        "permit_id": permit.json()["execution_permit_id"],
        "mission_id": mission_id,
        "action": action,
        "input": payload,
    }, headers=headers)
    return executed


def test_api_approved_gmail_workflow_sends_once_and_records_execution(tmp_path, monkeypatch):
    client, transport, provider, journal = _wire_fake_gmail(monkeypatch, tmp_path)
    try:
        response = _approved_send(client, PAYLOAD, "mission-gmail-e2e-1")
        assert response.status_code == 200, response.text
        body = response.json()["execution"]
        assert body["status"] == "succeeded"
        assert body["result"]["status"] == "sent"
        assert body["result"]["provider_message_id"] == "gmail-message-123"
        assert len(transport.gmail_calls) == 1
        stored = provider.ledger.status_for_key("google-sub-123", PAYLOAD["idempotency_key"])
        assert stored["status"] == "sent"
        assert journal.get(body["execution_id"])["status"] == "succeeded"
    finally:
        client.close()


def test_api_replay_of_successful_idempotency_key_does_not_call_google_twice(tmp_path, monkeypatch):
    client, transport, provider, _ = _wire_fake_gmail(monkeypatch, tmp_path)
    try:
        first = _approved_send(client, PAYLOAD, "mission-gmail-e2e-replay-1")
        second = _approved_send(client, PAYLOAD, "mission-gmail-e2e-replay-2")
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["execution"]["result"]["duplicate_replay"] is True
        assert len(transport.gmail_calls) == 1
        assert provider.ledger.status_for_key("google-sub-123", PAYLOAD["idempotency_key"])["status"] == "sent"
    finally:
        client.close()


def test_api_rejects_payload_tampering_after_approval_before_provider_call(tmp_path, monkeypatch):
    client, transport, _, _ = _wire_fake_gmail(monkeypatch, tmp_path)
    try:
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
        operator_headers = {"Authorization": f"Bearer {OPERATOR_KEY}"}
        preview = client.post("/api/v1/connectors/gmail.email/action-preview", json=PAYLOAD, headers=headers)
        action = preview.json()["action"]
        mission_id = "mission-gmail-e2e-tamper"
        created = client.post("/api/v1/missions", json={
            "mission_id": mission_id, "objective": "Test tamper rejection",
            "allowed_tools": ["gmail.email"], "forbidden_actions": [], "budget": 0,
        }, headers=operator_headers)
        assert created.status_code == 200
        proposed = client.post(f"/api/v1/missions/{mission_id}/actions", json={"action": action}, headers=operator_headers)
        approval_id = proposed.json()["approval"]["approval_id"]
        assert client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approved"}, headers=headers).status_code == 200
        permit = client.post(f"/api/v1/approvals/{approval_id}/execution-permit", json={"mission_id": mission_id, "action": action}, headers=headers)
        tampered = {**PAYLOAD, "body": "Changed after approval"}
        response = client.post("/api/v1/connectors/gmail.email/execute", json={
            "permit_id": permit.json()["execution_permit_id"], "mission_id": mission_id,
            "action": action, "input": tampered,
        }, headers=headers)
        assert response.status_code == 400
        assert "does not match" in response.json()["detail"]
        assert transport.gmail_calls == []
    finally:
        client.close()
