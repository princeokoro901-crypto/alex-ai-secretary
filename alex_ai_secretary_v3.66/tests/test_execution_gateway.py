import pytest
from app.approvals import ApprovalService, ApprovalError
from app.execution import ExecutionGateway, ExecutionError

OWNER = {"actor_id": "requester", "key_id": "request-key", "role": "operator"}
ADMIN = {"actor_id": "reviewer", "key_id": "review-key", "role": "admin"}
EXECUTOR = {"actor_id": "executor", "key_id": "executor-key", "role": "operator"}


def approved_service(tmp_path):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    record = service.create("mission-1", "Prepare report", OWNER)
    service.decide(record.approval_id, "approved", ADMIN)
    permit = service.issue_execution_permit(record.approval_id, "mission-1", "Prepare report", EXECUTOR)
    return service, permit.execution_permit_id


def test_gateway_executes_only_registered_handler_once(tmp_path):
    service, permit = approved_service(tmp_path)
    calls = []
    events = []
    gateway = ExecutionGateway(service, lambda *args: events.append(args))
    gateway.register("report", lambda payload: calls.append(payload) or {"created": True})
    result = gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
        action_type="report", payload={"title": "Weekly"}, actor=EXECUTOR)
    assert result.status == "succeeded"
    assert result.result == {"created": True}
    assert calls == [{"title": "Weekly"}]
    assert events[0][0] == "execution_succeeded"
    with pytest.raises(ExecutionError):
        gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
            action_type="report", payload={}, actor=EXECUTOR)
    assert len(calls) == 1


def test_gateway_does_not_call_handler_for_wrong_action_or_unknown_handler(tmp_path):
    service, permit = approved_service(tmp_path)
    calls = []
    gateway = ExecutionGateway(service)
    gateway.register("report", lambda payload: calls.append(payload))
    with pytest.raises(ExecutionError):
        gateway.execute(permit_id=permit, mission_id="mission-1", action="Alter report",
            action_type="report", payload={}, actor=EXECUTOR)
    assert calls == []
    # A failed validation must not consume the permit; unknown handlers are rejected before consumption.
    with pytest.raises(ExecutionError):
        gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
            action_type="email", payload={}, actor=EXECUTOR)
    result = gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
        action_type="report", payload={}, actor=EXECUTOR)
    assert result.status == "succeeded"


def test_handler_failure_is_recorded_and_permit_is_not_retried(tmp_path):
    service, permit = approved_service(tmp_path)
    gateway = ExecutionGateway(service)
    def fail(_payload):
        raise RuntimeError("connector unavailable")
    gateway.register("report", fail)
    result = gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
        action_type="report", payload={}, actor=EXECUTOR)
    assert result.status == "failed"
    assert "connector unavailable" in result.error
    with pytest.raises(ExecutionError):
        gateway.execute(permit_id=permit, mission_id="mission-1", action="Prepare report",
            action_type="report", payload={}, actor=EXECUTOR)


def test_duplicate_handler_registration_is_rejected(tmp_path):
    gateway = ExecutionGateway(ApprovalService(str(tmp_path / "approvals.json")))
    gateway.register("report", lambda _: None)
    with pytest.raises(ValueError):
        gateway.register("report", lambda _: None)
