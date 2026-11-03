import json

import pytest

from app.agents.runtime import AgentRuntime
from app.audit import AuditLog
from app.missions.models import MissionService
from app.orchestrator.service import (
    OrchestrationRequest,
    OrchestrationResult,
    OrchestrationStateStore,
    OrchestratorService,
    SQLiteOrchestrationStateStore,
)
from app.reconciliation.service import LifecycleReconciliationService
from app.routing.service import AgentRoutingService
from app.workforce.service import WorkforceService


STORE_TYPES = [OrchestrationStateStore, SQLiteOrchestrationStateStore]


def make_service(store, audit=None, runtime_override=None):
    workforce = WorkforceService()
    missions = MissionService(workforce=workforce)
    runtime = AgentRuntime(workforce, missions)
    router = AgentRoutingService(workforce, missions, runtime.performance)
    service = OrchestratorService(
        workforce, missions, router, runtime_override or runtime, store, audit
    )
    return workforce, missions, runtime, service


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_interrupted_running_record_is_recovered_without_retry(tmp_path, store_type):
    path = tmp_path / ("orchestration.db" if store_type is SQLiteOrchestrationStateStore else "orchestration.json")
    store = store_type(str(path))
    request = OrchestrationRequest(
        "Inspect safely", "research", ["search_web"], "search_web",
        idempotency_key="stable-key",
    )
    running = OrchestrationResult(
        orchestration_id="orchestration-crashed",
        mission_id="mission-crashed",
        status="running",
        selected_agent_id="research",
        result={"message": "in progress"},
        created_at="2026-10-24T00:00:00+00:00",
        updated_at="2026-10-24T00:00:00+00:00",
        recovery_state="execution_pending",
        request_metadata=OrchestratorService._request_metadata(request, "mission-crashed"),
    )
    store.save({running.orchestration_id: running}, {"stable-key": running.orchestration_id})

    _, _, runtime, service = make_service(store)
    recovered = service.runs[running.orchestration_id]
    assert recovered.status == "uncertain"
    assert recovered.recovery_state == "interrupted_after_restart"
    assert recovered.result["recovery"]["automatic_retry"] is False
    assert recovered.request_metadata["objective"] == "Inspect safely"

    replay = service.run(request)
    assert replay.orchestration_id == running.orchestration_id
    assert replay.status == "uncertain"
    assert runtime.tasks == {}


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_runtime_exception_becomes_uncertain_and_same_key_does_not_execute_again(tmp_path, store_type):
    path = tmp_path / ("orchestration.db" if store_type is SQLiteOrchestrationStateStore else "orchestration.json")
    store = store_type(str(path))
    workforce = WorkforceService()
    missions = MissionService(workforce=workforce)
    runtime = AgentRuntime(workforce, missions)
    router = AgentRoutingService(workforce, missions, runtime.performance)

    class RaisingRuntime:
        performance = runtime.performance

        def submit(self, **kwargs):
            raise RuntimeError("simulated runtime interruption")

    service = OrchestratorService(workforce, missions, router, RaisingRuntime(), store)
    request = OrchestrationRequest(
        "Research a topic", "research", ["search_web"], "search_web",
        input={"context": "private input should not be copied into recovery metadata"},
        idempotency_key="runtime-exception-key",
    )
    result = service.run(request)
    assert result.status == "uncertain"
    assert result.recovery_state == "runtime_exception"
    assert result.result["recovery"]["automatic_retry"] is False

    # The state file/database stores a digest and input key names, not the raw input value.
    if store_type is OrchestrationStateStore:
        persisted_text = path.read_text(encoding="utf-8")
        assert "private input should not be copied" not in persisted_text

    _, _, runtime_after_restart, restarted = make_service(store)
    replay = restarted.run(request)
    assert replay.orchestration_id == result.orchestration_id
    assert replay.status == "uncertain"
    assert runtime_after_restart.tasks == {}


def test_intent_and_idempotency_mapping_are_persisted_before_runtime(tmp_path):
    path = tmp_path / "orchestration.json"
    store = OrchestrationStateStore(str(path))
    workforce = WorkforceService()
    missions = MissionService(workforce=workforce)
    runtime = AgentRuntime(workforce, missions)
    router = AgentRoutingService(workforce, missions, runtime.performance)

    class InspectingRuntime:
        performance = runtime.performance

        def submit(self, **kwargs):
            runs, keys = store.load()
            assert len(runs) == 1
            record = next(iter(runs.values()))
            assert record.status == "running"
            assert keys["pre-runtime-key"] == record.orchestration_id
            return runtime.submit(**kwargs)

    service = OrchestratorService(workforce, missions, router, InspectingRuntime(), store)
    result = service.run(OrchestrationRequest(
        "Research", "research", ["search_web"], "search_web", budget=1,
        input={"context": "private"}, idempotency_key="pre-runtime-key",
    ))
    assert result.status == "completed"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    saved = persisted["runs"][result.orchestration_id]
    assert saved["request_metadata"]["input_keys"] == ["context"]
    assert "private" not in json.dumps(saved["request_metadata"])


def test_restart_recovery_writes_terminal_audit_event_and_reconciles(tmp_path):
    path = tmp_path / "orchestration.json"
    store = OrchestrationStateStore(str(path))
    record = OrchestrationResult(
        orchestration_id="audited-crash", mission_id="mission-audited-crash",
        status="running", selected_agent_id="research", result={},
        recovery_state="execution_pending",
    )
    store.save({record.orchestration_id: record}, {})
    audit = AuditLog()
    audit.record("orchestration_started", "system", "orchestration", record.orchestration_id, {})

    _, _, _, service = make_service(store, audit=audit)
    assert service.runs[record.orchestration_id].status == "uncertain"
    assert any(event.event_type == "orchestration_interrupted" for event in audit.events)
    report = LifecycleReconciliationService().reconcile(service.runs, audit.events)
    assert report["consistent"] is True
    assert report["recoverable_count"] == 0


def test_reusing_idempotency_key_with_different_request_is_rejected(tmp_path):
    store = OrchestrationStateStore(str(tmp_path / "orchestration.json"))
    _, _, runtime, service = make_service(store)
    original = OrchestrationRequest(
        "Research topic A", "research", ["search_web"], "search_web",
        idempotency_key="same-key",
    )
    first = service.run(original)
    before = len(runtime.tasks)
    altered = OrchestrationRequest(
        "Research topic B", "research", ["search_web"], "search_web",
        idempotency_key="same-key",
    )
    with pytest.raises(ValueError, match="different request"):
        service.run(altered)
    assert len(runtime.tasks) == before == 1
    assert first.status == "completed"


@pytest.mark.parametrize("store_type", STORE_TYPES)
def test_recovery_review_is_immutable_and_survives_persistent_store_reload(tmp_path, store_type):
    from app.orchestrator.service import RecoveryReviewAuditError
    from app.audit import AuditLog

    path = tmp_path / ("review.db" if store_type is SQLiteOrchestrationStateStore else "review.json")
    store = store_type(str(path))
    audit = AuditLog()
    _, _, _, service = make_service(store, audit=audit)
    record = OrchestrationResult(
        orchestration_id="uncertain-review-case", mission_id="mission-review-case",
        status="uncertain", selected_agent_id="research", result={"recovery": {"automatic_retry": False}},
        recovery_state="interrupted_after_restart",
    )
    service.runs[record.orchestration_id] = record
    service._persist()
    actor = {"actor_id": "operator-7", "key_id": "operator-key-7", "role": "operator"}

    reviewed = service.record_recovery_review(record.orchestration_id, "acknowledged_no_retry", actor)
    assert reviewed.status == "uncertain"
    assert reviewed.recovery_review["disposition"] == "acknowledged_no_retry"
    assert reviewed.recovery_review["reviewed_by"] == "operator-7"
    assert any(event.event_type == "orchestration_recovery_reviewed" for event in audit.events)
    with pytest.raises(ValueError, match="already been recorded"):
        service.record_recovery_review(record.orchestration_id, "escalated_for_manual_investigation", actor)

    _, _, _, restarted = make_service(store)
    persisted = restarted.runs[record.orchestration_id]
    assert persisted.status == "uncertain"
    assert persisted.recovery_review["disposition"] == "acknowledged_no_retry"


def test_recovery_review_rejects_non_uncertain_orchestration():
    _, _, _, service = make_service(OrchestrationStateStore())
    record = OrchestrationResult(
        orchestration_id="completed-review-case", mission_id="mission-completed-review",
        status="completed", selected_agent_id="research", result={},
    )
    service.runs[record.orchestration_id] = record
    with pytest.raises(ValueError, match="Only uncertain"):
        service.record_recovery_review(record.orchestration_id, "acknowledged_no_retry",
                                       {"actor_id": "operator", "key_id": "key", "role": "operator"})


def test_recovery_review_rejects_unknown_disposition():
    _, _, _, service = make_service(OrchestrationStateStore())
    record = OrchestrationResult(
        orchestration_id="uncertain-invalid-review", mission_id="mission-invalid-review",
        status="uncertain", selected_agent_id="research", result={},
    )
    service.runs[record.orchestration_id] = record
    with pytest.raises(ValueError, match="Unsupported recovery disposition"):
        service.record_recovery_review(record.orchestration_id, "retry_now",
                                       {"actor_id": "operator", "key_id": "key", "role": "operator"})


def test_recovery_review_store_failure_does_not_leave_phantom_in_memory_review():
    class FailingStore:
        def load(self):
            return {}, {}
        def save(self, runs, idempotency):
            raise OSError("simulated state-store failure")

    _, _, _, service = make_service(FailingStore())
    record = OrchestrationResult(
        orchestration_id="review-store-failure", mission_id="mission-review-store-failure",
        status="uncertain", selected_agent_id="research", result={},
    )
    service.runs[record.orchestration_id] = record
    with pytest.raises(OSError, match="simulated state-store failure"):
        service.record_recovery_review(record.orchestration_id, "acknowledged_no_retry",
                                       {"actor_id": "operator", "key_id": "key", "role": "operator"})
    assert record.recovery_review is None


def test_recovery_review_audit_failure_keeps_saved_review_and_blocks_duplicate(tmp_path):
    from app.orchestrator.service import RecoveryReviewAuditError

    class BrokenAudit:
        def record(self, *args, **kwargs):
            raise OSError("simulated audit append failure")

    store = OrchestrationStateStore(str(tmp_path / "audit-failure.json"))
    _, _, _, service = make_service(store, audit=BrokenAudit())
    record = OrchestrationResult(
        orchestration_id="review-audit-failure", mission_id="mission-review-audit-failure",
        status="uncertain", selected_agent_id="research", result={},
    )
    service.runs[record.orchestration_id] = record
    service._persist()
    actor = {"actor_id": "operator", "key_id": "operator-key", "role": "operator"}

    with pytest.raises(RecoveryReviewAuditError, match="Review was saved"):
        service.record_recovery_review(record.orchestration_id, "acknowledged_no_retry", actor)
    assert record.recovery_review["disposition"] == "acknowledged_no_retry"
    reloaded, _ = store.load()
    assert reloaded[record.orchestration_id].recovery_review["reviewed_by"] == "operator"
    with pytest.raises(ValueError, match="already been recorded"):
        service.record_recovery_review(record.orchestration_id, "acknowledged_no_retry", actor)
