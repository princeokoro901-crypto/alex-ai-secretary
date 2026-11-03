import pytest
from app.approvals import ApprovalService
from app.connectors.registry import ConnectorManifest, ConnectorRegistry
from app.execution import ExecutionError, ExecutionGateway

OWNER = {"actor_id": "requester", "key_id": "request-key", "role": "operator"}
ADMIN = {"actor_id": "reviewer", "key_id": "review-key", "role": "admin"}
EXECUTOR = {"actor_id": "executor", "key_id": "executor-key", "role": "operator"}


def approved_service(tmp_path, action="Send report email"):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    record = service.create("mission-1", action, OWNER)
    service.decide(record.approval_id, "approved", ADMIN)
    permit = service.issue_execution_permit(record.approval_id, "mission-1", action, EXECUTOR)
    return service, permit.execution_permit_id


def manifest(**overrides):
    values = dict(connector_id="demo.email", name="Demo email", description="Sends a test email",
                  action_type="email.send", input_fields={"to": "string", "subject": "string", "body": "string"},
                  required_fields=("to", "subject", "body"), allowed_roles=("operator", "admin"))
    values.update(overrides)
    return ConnectorManifest(**values)


def test_connector_executes_through_approval_gateway(tmp_path):
    service, permit = approved_service(tmp_path)
    gateway = ExecutionGateway(service)
    sent = []
    registry = ConnectorRegistry(gateway)
    registry.register(manifest(), lambda payload: sent.append(payload) or {"queued": True})
    result = registry.execute(connector_id="demo.email", permit_id=permit, mission_id="mission-1",
                              action="Send report email", payload={"to": "test@example.com", "subject": "Hi", "body": "Report"},
                              actor=EXECUTOR)
    assert result.status == "succeeded"
    assert sent == [{"to": "test@example.com", "subject": "Hi", "body": "Report"}]
    assert registry.list()[0]["execution_policy"] == "approval-bound single-use permit required"


def test_connector_rejects_invalid_input_before_consuming_permit(tmp_path):
    service, permit = approved_service(tmp_path)
    gateway = ExecutionGateway(service)
    calls = []
    registry = ConnectorRegistry(gateway)
    registry.register(manifest(), lambda payload: calls.append(payload))
    base = dict(connector_id="demo.email", permit_id=permit, mission_id="mission-1", action="Send report email", actor=EXECUTOR)
    with pytest.raises(ExecutionError, match="Missing required"):
        registry.execute(**base, payload={"to": "test@example.com"})
    with pytest.raises(ExecutionError, match="Unknown connector input field"):
        registry.execute(**base, payload={"to": "test@example.com", "subject": "Hi", "body": "x", "shell": "bad"})
    with pytest.raises(ExecutionError, match="wrong type"):
        registry.execute(**base, payload={"to": "test@example.com", "subject": 3, "body": "x"})
    assert calls == []
    # Input validation failed before the single-use approval permit was consumed.
    result = registry.execute(**base, payload={"to": "test@example.com", "subject": "Hi", "body": "x"})
    assert result.status == "succeeded"


def test_connector_role_and_unknown_connector_are_denied(tmp_path):
    service, permit = approved_service(tmp_path)
    registry = ConnectorRegistry(ExecutionGateway(service))
    registry.register(manifest(allowed_roles=("admin",)), lambda _: {"ok": True})
    with pytest.raises(ExecutionError, match="Unknown connector"):
        registry.execute(connector_id="missing", permit_id=permit, mission_id="mission-1", action="Send report email", payload={}, actor=ADMIN)
    with pytest.raises(ExecutionError, match="role is not allowed"):
        registry.execute(connector_id="demo.email", permit_id=permit, mission_id="mission-1", action="Send report email",
                         payload={"to": "x", "subject": "s", "body": "b"}, actor=EXECUTOR)
    # Rejected use did not consume the permit.
    result = registry.execute(connector_id="demo.email", permit_id=permit, mission_id="mission-1", action="Send report email",
                              payload={"to": "x", "subject": "s", "body": "b"}, actor=ADMIN)
    assert result.status == "succeeded"


def test_registry_rejects_unsafe_or_duplicate_manifest(tmp_path):
    registry = ConnectorRegistry(ExecutionGateway(ApprovalService(str(tmp_path / "approvals.json"))))
    with pytest.raises(ValueError, match="namespaced"):
        registry.register(manifest(action_type="send_email"), lambda _: None)
    with pytest.raises(ValueError, match="required field"):
        registry.register(manifest(required_fields=("not_declared",)), lambda _: None)
    registry.register(manifest(), lambda _: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(manifest(), lambda _: None)


def test_action_builder_binds_approved_action_to_exact_payload(tmp_path):
    from app.connectors.sandbox_email import SandboxEmailOutbox, sandbox_email_action
    original = {"to": "person@example.com", "subject": "Report", "body": "Approved content"}
    approved_action = sandbox_email_action(original)
    service = ApprovalService(str(tmp_path / "approvals-bound-email.json"))
    approval = service.create("mission-1", approved_action, OWNER)
    service.decide(approval.approval_id, "approved", ADMIN)
    permit_record = service.issue_execution_permit(approval.approval_id, "mission-1", approved_action, EXECUTOR)
    gateway = ExecutionGateway(service)
    outbox = SandboxEmailOutbox()
    registry = ConnectorRegistry(gateway)
    email_manifest = ConnectorManifest(
        connector_id="sandbox.email", name="Sandbox email", description="Simulation only",
        action_type="email.send_sandbox",
        input_fields={"to": "string", "subject": "string", "body": "string"},
        required_fields=("to", "subject", "body"),
    )
    registry.register(email_manifest, outbox.queue_simulated, action_builder=sandbox_email_action)
    tampered = {"to": "other@example.com", "subject": "Report", "body": "Approved content"}
    with pytest.raises(ExecutionError, match="does not match this connector payload"):
        registry.execute(connector_id="sandbox.email", permit_id=permit_record.execution_permit_id,
                         mission_id="mission-1", action=approved_action, payload=tampered, actor=EXECUTOR)
    assert outbox.list_messages() == []
    result = registry.execute(connector_id="sandbox.email", permit_id=permit_record.execution_permit_id,
                              mission_id="mission-1", action=approved_action, payload=original, actor=EXECUTOR)
    assert result.status == "succeeded"
    assert outbox.list_messages()[0]["recipient"] == "person@example.com"
    outbox.close()


def test_sandbox_email_action_changes_when_content_changes():
    from app.connectors.sandbox_email import sandbox_email_action
    payload = {"to": "person@example.com", "subject": "Report", "body": "Version A"}
    action = sandbox_email_action(payload)
    changed = {**payload, "body": "Version B"}
    assert sandbox_email_action(changed) != action
    assert "Version A" not in action
