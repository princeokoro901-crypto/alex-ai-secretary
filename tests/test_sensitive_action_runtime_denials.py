"""Runtime authorization regression tests for sensitive API operations.

Requests are sent through FastAPI's HTTP middleware with role-scoped test keys.
They must be denied before endpoint logic can mutate mission, approval, execution,
or audit state. No external provider or connector dispatch is configured here.
"""
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app, audit_log, approval_service, mission_service
from app.security import reset_rate_limiter_for_tests


def _headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {role}-runtime-denial-secret"}


def test_sensitive_operations_are_denied_before_state_or_audit_mutation(monkeypatch):
    reset_rate_limiter_for_tests()
    entries = [
        {"key": "admin-runtime-denial-secret", "key_id": "runtime-admin", "actor_id": "runtime-admin", "role": "admin"},
        {"key": "operator-runtime-denial-secret", "key_id": "runtime-operator", "actor_id": "runtime-operator", "role": "operator"},
        {"key": "reader-runtime-denial-secret", "key_id": "runtime-reader", "actor_id": "runtime-reader", "role": "reader"},
    ]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)

    mission_id = f"runtime-denial-{uuid4().hex}"
    created = client.post(
        "/api/v1/missions",
        headers=_headers("operator"),
        json={"mission_id": mission_id, "objective": "Authorization denial regression test", "budget": 1},
    )
    assert created.status_code == 200, created.text
    before_mission = mission_service.get(mission_id)
    before_audit = len(audit_log.events)
    before_approvals = [record.__dict__.copy() for record in approval_service.list()]

    denied_requests = [
        ("mission start", "POST", f"/api/v1/missions/{mission_id}/start", None, "reader"),
        ("agent execution", "POST", f"/api/v1/missions/{mission_id}/agents/research/execute",
         {"objective": "must not run", "tool": "research", "input": {}, "budget": 0}, "reader"),
        ("execution permit issuance", "POST", f"/api/v1/approvals/nonexistent-{uuid4().hex}/execution-permit",
         {"mission_id": mission_id, "action": "send_email"}, "reader"),
        ("execution reconciliation", "POST", f"/api/v1/executions/nonexistent-{uuid4().hex}/reconcile",
         {"outcome": "completed", "reason": "test only"}, "operator"),
    ]

    for label, method, path, payload, role in denied_requests:
        response = client.request(method, path, headers=_headers(role), json=payload)
        assert response.status_code == 403, f"{label}: expected 403, got {response.status_code}: {response.text}"

    after_mission = mission_service.get(mission_id)
    assert after_mission.status == before_mission.status, "Denied requests changed mission status"
    assert after_mission.assigned_agents == before_mission.assigned_agents, "Denied requests changed mission assignment"
    assert len(audit_log.events) == before_audit, "Denied requests unexpectedly emitted audit events"
    assert [record.__dict__.copy() for record in approval_service.list()] == before_approvals, (
        "Denied requests changed approval or execution-permit state"
    )
    reset_rate_limiter_for_tests()
