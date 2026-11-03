"""v3.62 isolated-store approval and single-use permit lifecycle contracts."""
import pytest

from app.approvals import ApprovalError, ApprovalService


def actor(actor_id, key_id, role):
    return {"actor_id": actor_id, "key_id": key_id, "role": role}


def test_approval_and_permit_transitions_survive_service_restart(tmp_path):
    path = tmp_path / "approvals.json"
    requester = actor("requester", "requester-key", "operator")
    admin = actor("approver", "admin-key", "admin")
    executor = actor("executor", "executor-key", "operator")
    service = ApprovalService(str(path))

    proposal = service.create("mission-persist", "Prepare report", requester)
    approved = service.decide(proposal.approval_id, "approved", admin)
    assert approved.status == "approved"

    issued = service.issue_execution_permit(proposal.approval_id, "mission-persist", "Prepare report", executor)
    assert issued.execution_permit_id
    assert issued.execution_permit_issued_by == "executor"

    reopened = ApprovalService(str(path))
    loaded = reopened.get(proposal.approval_id)
    assert loaded.status == "approved"
    assert loaded.decided_by == "approver"
    assert loaded.execution_permit_id == issued.execution_permit_id
    assert loaded.execution_permit_consumed_at is None

    consumed = reopened.consume_execution_permit(issued.execution_permit_id, "mission-persist", "Prepare report", executor)
    assert consumed.execution_permit_consumed_by == "executor"
    again = ApprovalService(str(path)).get(proposal.approval_id)
    assert again.execution_permit_consumed_at
    assert again.execution_permit_consumed_key_id == "executor-key"


def test_wrong_actor_cannot_consume_permit_and_cannot_mutate_state(tmp_path):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    proposal = service.create("mission-bound", "Send approved notice", actor("requester", "r-key", "operator"))
    service.decide(proposal.approval_id, "approved", actor("admin", "a-key", "admin"))
    issued = service.issue_execution_permit(proposal.approval_id, "mission-bound", "Send approved notice", actor("operator", "o-key", "operator"))
    before = service.get(proposal.approval_id).__dict__.copy()

    with pytest.raises(ApprovalError, match="Only an admin or operator"):
        service.consume_execution_permit(issued.execution_permit_id, "mission-bound", "Send approved notice", actor("reader", "reader-key", "reader"))
    assert service.get(proposal.approval_id).__dict__ == before

    with pytest.raises(ApprovalError, match="does not match"):
        service.consume_execution_permit(issued.execution_permit_id, "mission-bound", "Different action", actor("operator", "o-key", "operator"))
    assert service.get(proposal.approval_id).__dict__ == before


def test_permit_cannot_be_reissued_or_consumed_twice(tmp_path):
    service = ApprovalService(str(tmp_path / "approvals.json"))
    proposal = service.create("mission-once", "Create local draft", actor("requester", "r-key", "operator"))
    service.decide(proposal.approval_id, "approved", actor("admin", "a-key", "admin"))
    executor = actor("operator", "o-key", "operator")
    issued = service.issue_execution_permit(proposal.approval_id, "mission-once", "Create local draft", executor)

    with pytest.raises(ApprovalError, match="already been issued"):
        service.issue_execution_permit(proposal.approval_id, "mission-once", "Create local draft", executor)
    service.consume_execution_permit(issued.execution_permit_id, "mission-once", "Create local draft", executor)
    snapshot = service.get(proposal.approval_id).__dict__.copy()
    with pytest.raises(ApprovalError, match="already been consumed"):
        service.consume_execution_permit(issued.execution_permit_id, "mission-once", "Create local draft", executor)
    assert service.get(proposal.approval_id).__dict__ == snapshot


def test_reconciliation_audit_write_failure_is_reported_and_partial_commit_is_visible(tmp_path, monkeypatch):
    """Characterize cross-store failure boundary; the journal and audit are not atomic."""
    import json
    from fastapi.testclient import TestClient
    import app.main as main_module
    from app.execution_journal import ExecutionJournal
    from app.security import reset_rate_limiter_for_tests

    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "v361-admin-secret", "key_id": "v361-admin-key", "actor_id": "v361-admin", "role": "admin"}
    ]))
    journal_path = str(tmp_path / "api-executions.sqlite3")
    journal = ExecutionJournal(journal_path)
    journal.begin("v361-uncertain", "permit", "mission", "digest", "local.test", {"actor_id": "worker", "key_id": "wk"})
    journal = ExecutionJournal(journal_path)  # startup recovery converts running to uncertain
    monkeypatch.setattr(main_module, "execution_journal", journal)

    def fail_audit(*args, **kwargs):
        raise OSError("simulated audit disk failure")

    monkeypatch.setattr(main_module.audit_log, "record", fail_audit)
    client = TestClient(main_module.app)
    response = client.post(
        "/api/v1/executions/v361-uncertain/reconcile",
        headers={"Authorization": "Bearer v361-admin-secret"},
        json={"outcome": "confirmed_failed", "reason": "isolated audit failure test"},
    )
    assert response.status_code == 503
    # Important known limitation: SQLite commits before the separate audit append.
    # The failure is surfaced, but the reconciliation row remains and requires review.
    records = journal.reconciliations_for("v361-uncertain")
    assert len(records) == 1
    assert records[0]["outcome"] == "confirmed_failed"
    reset_rate_limiter_for_tests()
