import json
from fastapi.testclient import TestClient
import app.main as main_module
from app.audit import AuditLog
from app.missions.models import MissionContract, MissionService
from app.security import reset_rate_limiter_for_tests


def test_recovery_queue_and_decision_api_are_authorized_audited_and_non_resuming(tmp_path, monkeypatch):
    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-secret", "key_id": "op-key", "actor_id": "prince-operator", "role": "operator"},
        {"key": "reader-secret", "key_id": "read-key", "actor_id": "read-only", "role": "reader"},
    ]))
    db = tmp_path / "missions.sqlite3"
    service = MissionService(store_path=str(db))
    service.create(MissionContract("recover-api-1", "Review interrupted work"))
    service.assign_agent("recover-api-1", "research")
    service.start("recover-api-1")
    service.close()
    service = MissionService(store_path=str(db))  # startup converts running to paused/review-required
    monkeypatch.setattr(main_module, "mission_service", service)
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(main_module, "audit_log", audit)
    client = TestClient(main_module.app)
    operator = {"Authorization": "Bearer operator-secret"}
    reader = {"Authorization": "Bearer reader-secret"}

    queue = client.get("/api/v1/missions/recovery-queue", headers=reader)
    assert queue.status_code == 200
    assert [m["contract"]["mission_id"] for m in queue.json()["missions"]] == ["recover-api-1"]

    denied = client.post("/api/v1/missions/recover-api-1/recovery-decision", headers=reader,
                         json={"decision": "stop", "reason": "not allowed"})
    assert denied.status_code == 403
    assert service.get("recover-api-1").recovery_decisions == []

    response = client.post("/api/v1/missions/recover-api-1/recovery-decision", headers=operator,
                           json={"decision": "keep_paused", "reason": "Need to reconcile external effects"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paused"
    assert response.json()["recovery_decisions"][0]["actor_id"] == "prince-operator"
    assert client.get("/api/v1/missions/recovery-queue", headers=operator).json()["missions"] == []
    assert audit.verify()
    event = next(e for e in audit.events if e.event_type == "mission_recovery_decision_recorded")
    assert event.actor == "prince-operator"
    assert event.details["key_id"] == "op-key"
    duplicate = client.post("/api/v1/missions/recover-api-1/recovery-decision", headers=operator,
                            json={"decision": "stop", "reason": "second attempt"})
    assert duplicate.status_code == 409
    service.close()
    reset_rate_limiter_for_tests()


def test_failed_audit_delivery_is_durable_and_retryable_without_repeating_decision(tmp_path, monkeypatch):
    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([
        {"key": "operator-secret", "key_id": "op-key", "actor_id": "prince-operator", "role": "operator"},
        {"key": "reader-secret", "key_id": "read-key", "actor_id": "read-only", "role": "reader"},
    ]))
    db = tmp_path / "missions.sqlite3"
    service = MissionService(store_path=str(db))
    service.create(MissionContract("recover-retry-1", "Review interrupted work"))
    service.assign_agent("recover-retry-1", "research")
    service.start("recover-retry-1")
    service.close()
    service = MissionService(store_path=str(db))
    monkeypatch.setattr(main_module, "mission_service", service)
    audit = AuditLog(str(tmp_path / "audit-retry.jsonl"))
    monkeypatch.setattr(main_module, "audit_log", audit)
    original_record = audit.record

    def fail_once(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(audit, "record", fail_once)
    client = TestClient(main_module.app)
    headers = {"Authorization": "Bearer operator-secret"}
    failed = client.post("/api/v1/missions/recover-retry-1/recovery-decision", headers=headers,
                         json={"decision": "keep_paused", "reason": "Wait for external reconciliation"})
    assert failed.status_code == 503
    saved = service.get("recover-retry-1")
    assert saved.status.value == "paused"
    assert len(saved.recovery_decisions) == 1
    assert saved.recovery_decisions[0]["audit_status"] == "pending"
    decision_id = saved.recovery_decisions[0]["decision_id"]

    # Operators can discover pending audit delivery; read-only users cannot see this operational queue.
    pending = client.get("/api/v1/missions/recovery-audit/pending", headers=headers)
    assert pending.status_code == 200, pending.text
    assert pending.json()["count"] == 1
    assert pending.json()["pending"][0]["mission_id"] == "recover-retry-1"
    assert pending.json()["pending"][0]["decision_id"] == decision_id
    assert pending.json()["pending"][0]["audit_status"] == "pending"
    denied_pending = client.get("/api/v1/missions/recovery-audit/pending", headers={"Authorization": "Bearer reader-secret"})
    assert denied_pending.status_code == 403

    # Simulate process restart: both the pending decision and its idempotency key survive.
    service.close()
    service = MissionService(store_path=str(db))
    monkeypatch.setattr(main_module, "mission_service", service)
    monkeypatch.setattr(audit, "record", original_record)
    retried = client.post("/api/v1/missions/recover-retry-1/recovery-audit/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["audit_status"] == "recorded"
    saved = service.get("recover-retry-1")
    assert len(saved.recovery_decisions) == 1
    assert saved.recovery_decisions[0]["decision_id"] == decision_id
    assert saved.recovery_decisions[0]["audit_status"] == "recorded"
    assert len([event for event in audit.events if event.event_id == decision_id]) == 1
    assert audit.verify()
    assert client.get("/api/v1/missions/recovery-audit/pending", headers=headers).json() == {"pending": [], "count": 0}

    again = client.post("/api/v1/missions/recover-retry-1/recovery-audit/retry", headers=headers)
    assert again.status_code == 200
    assert again.json()["already_recorded"] is True
    assert len([event for event in audit.events if event.event_id == decision_id]) == 1
    service.close()
    reset_rate_limiter_for_tests()
