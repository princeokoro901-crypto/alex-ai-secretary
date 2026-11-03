import json
import uuid
import pytest
from fastapi.testclient import TestClient

from app.approvals import ApprovalError, ApprovalService, ApprovalStoreError
from app.audit import AuditLog
from app.main import app
import app.main as main_module
from app.security import reset_rate_limiter_for_tests


def actor(actor_id="requester", key_id="request-key", role="operator"):
    return {"actor_id": actor_id, "key_id": key_id, "role": role}


def test_approval_requires_distinct_human_decider_and_persists(tmp_path):
    path = tmp_path / "approvals.json"
    service = ApprovalService(str(path))
    record = service.create("mission-1", "Send an email to the applicant", actor())
    assert record.status == "pending"
    with pytest.raises(ApprovalError, match="requester"):
        service.decide(record.approval_id, "approved", actor())
    with pytest.raises(ApprovalError, match="reason"):
        service.decide(record.approval_id, "rejected", actor("approver", "admin-key", "admin"))
    decided = service.decide(record.approval_id, "approved", actor("approver", "admin-key", "admin"))
    assert decided.status == "approved"
    assert decided.decided_by == "approver"
    assert decided.decider_key_id == "admin-key"
    loaded = ApprovalService(str(path))
    assert loaded.get(record.approval_id).status == "approved"
    assert loaded.list("pending") == []
    with pytest.raises(ApprovalError, match="Only pending"):
        loaded.decide(record.approval_id, "rejected", actor("another", "another-key", "admin"), "changed mind")


def test_rejection_requires_reason_and_is_persisted(tmp_path):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    record = service.create("mission-2", "Delete a record", actor())
    rejected = service.decide(record.approval_id, "rejected", actor("approver", "admin-key", "admin"), "Not authorized")
    assert rejected.status == "rejected"
    assert rejected.decision_reason == "Not authorized"


def test_corrupt_approval_store_fails_closed(tmp_path):
    path = tmp_path / "approvals.json"
    path.write_text('{broken', encoding="utf-8")
    with pytest.raises(ApprovalStoreError, match="refusing to reset"):
        ApprovalService(str(path))


def test_approval_action_api_enforces_separation_and_audit_attribution(tmp_path, monkeypatch):
    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "request-secret", "key_id": "request-key", "actor_id": "requester", "role": "admin"},
        {"key": "approve-secret", "key_id": "approve-key", "actor_id": "approver", "role": "admin"},
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator", "role": "operator"},
    ]))
    monkeypatch.setattr(main_module, "approval_service", ApprovalService(str(tmp_path / "approvals.json")))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(main_module, "audit_log", audit)
    client = TestClient(app)
    headers = {"Authorization": "Bearer request-secret"}
    mission_id = "approval-test-" + str(uuid.uuid4())
    created = client.post("/api/v1/missions", headers=headers, json={"mission_id": mission_id, "objective": "Prepare a message"})
    assert created.status_code == 200
    proposed = client.post(f"/api/v1/missions/{mission_id}/actions", headers=headers, json={"action": "Send an email"})
    assert proposed.status_code == 200
    body = proposed.json()
    assert body["approval"]["status"] == "pending"
    assert "does not execute" in body["note"]
    approval_id = body["approval"]["approval_id"]
    self_decision = client.post(f"/api/v1/approvals/{approval_id}/decision", headers=headers, json={"decision": "approved"})
    assert self_decision.status_code == 400
    operator_decision = client.post(f"/api/v1/approvals/{approval_id}/decision", headers={"Authorization": "Bearer operator-secret"}, json={"decision": "approved"})
    assert operator_decision.status_code == 403
    approved = client.post(f"/api/v1/approvals/{approval_id}/decision", headers={"Authorization": "Bearer approve-secret"}, json={"decision": "approved"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert audit.verify()
    requested_event = next(e for e in audit.events if e.event_type == "approval_requested")
    decided_event = next(e for e in audit.events if e.event_type == "approval_decided")
    assert requested_event.actor == "requester"
    assert requested_event.details["key_id"] == "request-key"
    assert decided_event.actor == "approver"
    assert decided_event.details["key_id"] == "approve-key"
    assert ApprovalService(str(tmp_path / "approvals.json")).get(approval_id).status == "approved"
    reset_rate_limiter_for_tests()


def test_orchestration_audit_records_authenticated_initiator(tmp_path):
    from app.audit import AuditLog
    from app.orchestrator.service import OrchestrationRequest, OrchestratorService, OrchestrationStateStore
    from app.workforce.service import WorkforceService
    from app.missions.models import MissionService
    from app.agents.runtime import AgentRuntime
    from app.handoffs.service import HandoffService
    from app.routing.service import AgentRoutingService

    workforce = WorkforceService()
    missions = MissionService(workforce=workforce)
    runtime = AgentRuntime(workforce, missions)
    router = AgentRoutingService(workforce, missions, runtime.performance)
    audit = AuditLog(str(tmp_path / "orchestration-audit.jsonl"))
    service = OrchestratorService(workforce, missions, router, runtime, OrchestrationStateStore(), audit)
    request = OrchestrationRequest(
        "Research", "research", ["search_web"], "search_web", budget=1,
        initiated_by="prince", initiated_by_key_id="phone-key", initiated_by_role="operator",
    )
    service.run(request)
    events = [e for e in audit.events if e.event_type in {"orchestration_started", "orchestration_completed"}]
    assert len(events) == 2
    assert all(e.actor == "prince" for e in events)
    assert all(e.details["key_id"] == "phone-key" for e in events)
    assert audit.verify()


def test_execution_permit_binds_exact_action_and_is_single_use(tmp_path):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    requester = actor("requester", "request-key", "operator")
    approver = actor("approver", "admin-key", "admin")
    executor = actor("executor", "executor-key", "operator")
    record = service.create("mission-guard", "Send the approved email", requester)

    with pytest.raises(ApprovalError, match="Only approved"):
        service.issue_execution_permit(record.approval_id, "mission-guard", "Send the approved email", executor)

    service.decide(record.approval_id, "approved", approver)
    with pytest.raises(ApprovalError, match="does not match"):
        service.issue_execution_permit(record.approval_id, "mission-guard", "Send a different email", executor)
    with pytest.raises(ApprovalError, match="does not match"):
        service.issue_execution_permit(record.approval_id, "another-mission", "Send the approved email", executor)

    issued = service.issue_execution_permit(record.approval_id, "mission-guard", "Send the approved email", executor)
    assert issued.execution_permit_id
    assert len(issued.action_digest) == 64
    with pytest.raises(ApprovalError, match="already been issued"):
        service.issue_execution_permit(record.approval_id, "mission-guard", "Send the approved email", executor)

    with pytest.raises(ApprovalError, match="does not match"):
        service.consume_execution_permit(issued.execution_permit_id, "mission-guard", "Send a different email", executor)
    consumed = service.consume_execution_permit(issued.execution_permit_id, "mission-guard", "Send the approved email", executor)
    assert consumed.execution_permit_consumed_by == "executor"
    assert consumed.execution_permit_consumed_key_id == "executor-key"
    with pytest.raises(ApprovalError, match="already been consumed"):
        service.consume_execution_permit(issued.execution_permit_id, "mission-guard", "Send the approved email", executor)

    reloaded = ApprovalService(str(tmp_path / "approvals.json"))
    assert reloaded.get(record.approval_id).execution_permit_consumed_at is not None


def test_execution_permit_api_requires_permission_and_does_not_execute(tmp_path, monkeypatch):
    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "request-secret", "key_id": "request-key", "actor_id": "requester", "role": "operator"},
        {"key": "approve-secret", "key_id": "approve-key", "actor_id": "approver", "role": "admin"},
        {"key": "reader-secret", "key_id": "reader-key", "actor_id": "reader", "role": "reader"},
    ]))
    monkeypatch.setattr(main_module, "approval_service", ApprovalService(str(tmp_path / "approvals.json")))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(main_module, "audit_log", audit)
    client = TestClient(app)
    operator_headers = {"Authorization": "Bearer request-secret"}
    mission_id = "permit-api-" + str(uuid.uuid4())
    created = client.post("/api/v1/missions", headers=operator_headers, json={"mission_id": mission_id, "objective": "Prepare an email"})
    assert created.status_code == 200
    proposed = client.post(f"/api/v1/missions/{mission_id}/actions", headers=operator_headers, json={"action": "Send approved email"})
    assert proposed.status_code == 200
    approval_id = proposed.json()["approval"]["approval_id"]

    pending = client.post(
        f"/api/v1/approvals/{approval_id}/execution-permit", headers=operator_headers,
        json={"mission_id": mission_id, "action": "Send approved email"},
    )
    assert pending.status_code == 400

    approved = client.post(
        f"/api/v1/approvals/{approval_id}/decision", headers={"Authorization": "Bearer approve-secret"},
        json={"decision": "approved"},
    )
    assert approved.status_code == 200

    reader_attempt = client.post(
        f"/api/v1/approvals/{approval_id}/execution-permit", headers={"Authorization": "Bearer reader-secret"},
        json={"mission_id": mission_id, "action": "Send approved email"},
    )
    assert reader_attempt.status_code == 403

    mismatch = client.post(
        f"/api/v1/approvals/{approval_id}/execution-permit", headers=operator_headers,
        json={"mission_id": mission_id, "action": "Send different email"},
    )
    assert mismatch.status_code == 400

    issued = client.post(
        f"/api/v1/approvals/{approval_id}/execution-permit", headers=operator_headers,
        json={"mission_id": mission_id, "action": "Send approved email"},
    )
    assert issued.status_code == 200
    body = issued.json()
    assert body["execution_permit_id"]
    assert "No external action has been executed" in body["note"]
    event = next(e for e in audit.events if e.event_type == "execution_permit_issued")
    assert event.actor == "requester"
    assert event.details["key_id"] == "request-key"
    assert audit.verify()
    reset_rate_limiter_for_tests()


def test_legacy_approval_record_loads_with_action_binding_migration(tmp_path):
    path = tmp_path / "approvals.json"
    service = ApprovalService(str(path))
    record = service.create("legacy-mission", "Archive draft", actor())
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in (
        "action_digest", "execution_permit_id", "execution_permit_issued_at",
        "execution_permit_issued_by", "execution_permit_issued_key_id",
        "execution_permit_consumed_at", "execution_permit_consumed_by",
        "execution_permit_consumed_key_id",
    ):
        payload["records"][0].pop(field, None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = ApprovalService(str(path)).get(record.approval_id)
    assert loaded.action_digest == ApprovalService._digest("legacy-mission", "Archive draft")



def test_distinct_non_admin_cannot_decide_even_when_service_called_directly():
    service = ApprovalService()
    record = service.create("mission-role", "Publish a post", actor("requester", "request-key", "operator"))
    with pytest.raises(ApprovalError, match="Only an admin"):
        service.decide(record.approval_id, "approved", actor("reviewer", "review-key", "operator"))
    assert service.get(record.approval_id).status == "pending"


def test_concurrent_permit_consumption_allows_only_one_success(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    service = ApprovalService(str(tmp_path / "approvals.json"))
    record = service.create("mission-race", "Release the report", actor("requester", "request-key", "operator"))
    service.decide(record.approval_id, "approved", actor("approver", "admin-key", "admin"))
    issued = service.issue_execution_permit(
        record.approval_id, "mission-race", "Release the report", actor("executor", "executor-key", "operator"),
    )

    def attempt(_):
        try:
            service.consume_execution_permit(
                issued.execution_permit_id, "mission-race", "Release the report",
                actor("executor", "executor-key", "operator"),
            )
            return "consumed"
        except ApprovalError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count("consumed") == 1
    assert outcomes.count("rejected") == 7
