from fastapi.testclient import TestClient
from app.main import app
from app.orchestrator.service import OrchestratorService, OrchestrationRequest


def fresh():
    from app.workforce.service import WorkforceService
    from app.missions.models import MissionService
    from app.agents.runtime import AgentRuntime
    from app.routing.service import AgentRoutingService
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    router = AgentRoutingService(w, m, r.performance)
    return OrchestratorService(w, m, router, r)


def test_orchestration_idempotency_returns_same_run():
    o = fresh()
    req = OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="k1")
    first = o.run(req)
    second = o.run(req)
    assert second.orchestration_id == first.orchestration_id
    assert len(o.runs) == 1


def test_api_can_retrieve_orchestration():
    client = TestClient(app)
    created = client.post("/api/v1/orchestrate", json={
        "objective":"Research", "task_type":"research", "required_tools":["search_web"],
        "tool":"search_web", "budget":1, "idempotency_key":"api-lifecycle-1"
    })
    assert created.status_code == 200
    oid = created.json()["orchestration_id"]
    fetched = client.get(f"/api/v1/orchestrations/{oid}")
    assert fetched.status_code == 200
    assert fetched.json()["orchestration_id"] == oid


def test_api_unknown_orchestration_is_404():
    client = TestClient(app)
    assert client.get("/api/v1/orchestrations/missing").status_code == 404


def test_consistency_report_detects_missing_audit():
    from app.audit import AuditLog
    o = fresh()
    req = OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="consistency-1")
    result = o.run(req)
    report = o.consistency_report([])
    assert not report["consistent"]
    assert {"type":"missing_audit", "orchestration_id":result.orchestration_id} in report["issues"]


def test_consistency_report_accepts_matching_completion():
    from app.audit import AuditLog
    o = fresh()
    log = AuditLog()
    req = OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="consistency-2")
    result = o.run(req)
    log.record("orchestration_completed", "system", "orchestration", result.orchestration_id, {"status": result.status})
    report = o.consistency_report(log.events)
    assert report["consistent"]


def test_consistency_report_detects_duplicate_completion():
    from app.audit import AuditLog
    o = fresh(); log = AuditLog()
    result = o.run(OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="consistency-3"))
    log.record("orchestration_completed", "system", "orchestration", result.orchestration_id)
    log.record("orchestration_completed", "system", "orchestration", result.orchestration_id)
    report = o.consistency_report(log.events)
    assert not report["consistent"]
    assert {"type":"duplicate_completion", "orchestration_id":result.orchestration_id} in report["issues"]


def test_api_lists_recent_orchestrations_read_only_and_respects_limit():
    from app.main import orchestrator_service
    before = len(orchestrator_service.runs)
    client = TestClient(app)
    created = client.post("/api/v1/orchestrate", json={
        "objective": "History list test", "task_type": "research",
        "required_tools": ["search_web"], "tool": "search_web", "budget": 0,
        "idempotency_key": "history-list-test-v338"
    })
    assert created.status_code == 200
    response = client.get("/api/v1/orchestrations?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert body["count"] == 1
    assert len(body["orchestrations"]) == 1
    assert body["orchestrations"][0]["orchestration_id"] == created.json()["orchestration_id"]
    assert len(orchestrator_service.runs) >= before + 1


def test_orchestration_history_limit_is_clamped():
    client = TestClient(app)
    assert client.get("/api/v1/orchestrations?limit=0").json()["limit"] == 1
    assert client.get("/api/v1/orchestrations?limit=500").json()["limit"] == 100


def test_recovery_review_api_records_actor_and_does_not_change_uncertain_status(monkeypatch):
    import json
    import app.main as main
    from app.audit import AuditLog
    from app.orchestrator.service import OrchestrationResult, OrchestrationStateStore

    service = fresh()
    service.audit_log = AuditLog()
    service.runs["api-uncertain-review"] = OrchestrationResult(
        orchestration_id="api-uncertain-review", mission_id="mission-api-uncertain-review",
        status="uncertain", selected_agent_id="research", result={},
        recovery_state="interrupted_after_restart",
    )
    service.state_store = OrchestrationStateStore()
    monkeypatch.setattr(main, "orchestrator_service", service)
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-review-secret", "key_id": "operator-review-key", "actor_id": "operator-42", "role": "operator"},
        {"key": "reader-review-secret", "key_id": "reader-review-key", "actor_id": "reader-42", "role": "reader"},
    ]))
    client = TestClient(app)

    denied = client.post("/api/v1/orchestrations/api-uncertain-review/recovery-review",
                         headers={"Authorization": "Bearer reader-review-secret"},
                         json={"disposition": "acknowledged_no_retry"})
    assert denied.status_code == 403
    assert service.runs["api-uncertain-review"].recovery_review is None

    response = client.post("/api/v1/orchestrations/api-uncertain-review/recovery-review",
                           headers={"Authorization": "Bearer operator-review-secret"},
                           json={"disposition": "acknowledged_no_retry"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "uncertain"
    assert body["recovery_review"]["reviewed_by"] == "operator-42"
    assert body["recovery_review"]["reviewer_key_id"] == "operator-review-key"
    assert any(event.event_type == "orchestration_recovery_reviewed" for event in service.audit_log.events)

    duplicate = client.post("/api/v1/orchestrations/api-uncertain-review/recovery-review",
                            headers={"Authorization": "Bearer operator-review-secret"},
                            json={"disposition": "escalated_for_manual_investigation"})
    assert duplicate.status_code == 409


def test_recovery_review_api_rejects_bad_disposition_and_unknown_run():
    client = TestClient(app)
    bad = client.post("/api/v1/orchestrations/missing/recovery-review", json={"disposition": "retry_now"})
    assert bad.status_code == 400
    missing = client.post("/api/v1/orchestrations/no-such-run/recovery-review", json={"disposition": "acknowledged_no_retry"})
    assert missing.status_code == 404
