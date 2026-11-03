from app.reconciliation.service import LifecycleReconciliationService
from app.audit import AuditEvent


def event(event_type, oid):
    return AuditEvent("e", event_type, "system", "orchestration", oid, "2026-01-01T00:00:00+00:00", {})


def test_reconciliation_is_consistent_for_started_and_completed_state():
    report = LifecycleReconciliationService().reconcile(
        {"o1": object()}, [event("orchestration_started", "o1"), event("orchestration_completed", "o1")]
    )
    assert report["consistent"] is True
    assert report["recoverable_count"] == 0
    assert report["inconsistent_count"] == 0


def test_reconciliation_marks_interrupted_start_recoverable():
    report = LifecycleReconciliationService().reconcile(
        {}, [event("orchestration_started", "o1")]
    )
    assert report["consistent"] is False
    assert report["recoverable_count"] == 1
    assert report["issues"][0]["type"] == "incomplete_lifecycle"


def test_reconciliation_detects_completed_without_state_as_inconsistent():
    report = LifecycleReconciliationService().reconcile(
        {}, [event("orchestration_started", "o1"), event("orchestration_completed", "o1")]
    )
    assert report["inconsistent_count"] == 1
    assert report["issues"][0]["type"] == "completed_without_state"


def test_reconciliation_does_not_mutate_stores():
    runs = {"o1": object()}
    audits = [event("orchestration_started", "o1")]
    LifecycleReconciliationService().reconcile(runs, audits)
    assert list(runs) == ["o1"]
    assert len(audits) == 1
