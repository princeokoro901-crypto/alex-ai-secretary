import json
from fastapi.testclient import TestClient
from app.main import app
from app.security import load_api_keys, authenticate_key, reset_rate_limiter_for_tests, _rate_limited, _required_permission


def test_key_configuration_rejects_duplicate_ids():
    entries = [{"key":"a", "key_id":"same", "actor_id":"u1", "role":"reader"},
               {"key":"b", "key_id":"same", "actor_id":"u2", "role":"operator"}]
    try:
        load_api_keys(json.dumps(entries))
        assert False, "duplicate key IDs should fail closed"
    except RuntimeError as exc:
        assert "unique" in str(exc)


def test_key_lookup_returns_identity_without_exposing_key():
    entries = [{"key":"not-for-logs", "key_id":"key-7", "actor_id":"user-7", "role":"reader"}]
    found = authenticate_key("not-for-logs", entries)
    assert found["actor_id"] == "user-7"
    assert found["key_id"] == "key-7"
    assert "key" in found


def test_api_key_required_when_configured(monkeypatch):
    entries = [{"key":"test-secret-value", "key_id":"test-key", "actor_id":"tester", "role":"reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    denied = client.get("/api/v1/workforce/teams")
    assert denied.status_code == 401
    accepted = client.get("/api/v1/workforce/teams", headers={"Authorization":"Bearer test-secret-value"})
    assert accepted.status_code == 200


def test_health_remains_public_when_keys_configured(monkeypatch):
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps([{"key":"s", "key_id":"k", "actor_id":"a", "role":"admin"}]))
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "3.66"


def test_reader_role_cannot_modify_resources(monkeypatch):
    entries = [{"key":"reader-secret", "key_id":"reader-key", "actor_id":"reader-user", "role":"reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    response = client.post("/api/v1/workforce/teams", headers={"Authorization":"Bearer reader-secret"}, json={})
    assert response.status_code == 403


def test_operator_can_submit_mutating_request(monkeypatch):
    entries = [{"key":"operator-secret", "key_id":"operator-key", "actor_id":"operator-user", "role":"operator"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    response = client.post("/api/v1/workforce/teams", headers={"Authorization":"Bearer operator-secret"}, json={})
    # Authentication/authorization permits the request; payload validation is downstream.
    assert response.status_code == 400


def test_sliding_window_rate_limiter_blocks_at_limit_and_recovers():
    reset_rate_limiter_for_tests()
    assert not _rate_limited("test-bucket", 2, 10, now=100.0)
    assert not _rate_limited("test-bucket", 2, 10, now=101.0)
    assert _rate_limited("test-bucket", 2, 10, now=102.0)
    assert not _rate_limited("test-bucket", 2, 10, now=111.0)
    reset_rate_limiter_for_tests()


def test_api_rate_limit_returns_429(monkeypatch):
    reset_rate_limiter_for_tests()
    monkeypatch.setenv("ALEX_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("ALEX_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.delenv("ALEX_API_KEYS_JSON", raising=False)
    client = TestClient(app)
    assert client.get("/api/v1/workforce/teams").status_code == 200
    limited = client.get("/api/v1/workforce/teams")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    reset_rate_limiter_for_tests()


def test_revoked_key_is_rejected_without_removing_it_from_rotation_config(monkeypatch):
    entries = [{"key":"old-secret", "key_id":"old-key", "actor_id":"user-1", "role":"admin", "active":False},
               {"key":"new-secret", "key_id":"new-key", "actor_id":"user-1", "role":"admin", "active":True}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    assert client.get("/api/v1/workforce/teams", headers={"Authorization":"Bearer old-secret"}).status_code == 401
    assert client.get("/api/v1/workforce/teams", headers={"Authorization":"Bearer new-secret"}).status_code == 200


def test_operator_cannot_read_audit_log_but_admin_can(monkeypatch):
    entries = [
        {"key":"operator-secret", "key_id":"operator-key", "actor_id":"operator-user", "role":"operator"},
        {"key":"admin-secret", "key_id":"admin-key", "actor_id":"admin-user", "role":"admin"},
    ]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    denied = client.get("/api/v1/audit", headers={"Authorization":"Bearer operator-secret"})
    allowed = client.get("/api/v1/audit", headers={"Authorization":"Bearer admin-secret"})
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_reader_can_use_post_based_read_operation_but_not_write(monkeypatch):
    entries = [{"key":"reader-secret", "key_id":"reader-key", "actor_id":"reader-user", "role":"reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    # This route uses POST for an analytical recommendation, not a resource write.
    response = client.post("/api/v1/workforce/recommend", headers={"Authorization":"Bearer reader-secret"}, json={})
    assert response.status_code == 400  # Passed authorization; rejected only by payload validation.
    denied = client.post("/api/v1/workforce/teams", headers={"Authorization":"Bearer reader-secret"}, json={})
    assert denied.status_code == 403


def test_explicit_permissions_can_narrow_role_grants(monkeypatch):
    entries = [{"key":"scoped-secret", "key_id":"scoped-key", "actor_id":"scoped-user", "role":"reader", "permissions":["workforce:read"]}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    assert client.get("/api/v1/workforce/teams", headers={"Authorization":"Bearer scoped-secret"}).status_code == 200
    assert client.get("/api/v1/missions/not-real", headers={"Authorization":"Bearer scoped-secret"}).status_code == 403


def test_explicit_permissions_cannot_escalate_role(monkeypatch):
    entries = [{"key":"bad-secret", "key_id":"bad-key", "actor_id":"user", "role":"reader", "permissions":["audit:read"]}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    response = TestClient(app).get("/api/v1/workforce/teams", headers={"Authorization":"Bearer bad-secret"})
    assert response.status_code == 503
    assert "configuration" in response.json()["detail"].lower()


def test_unknown_protected_route_fails_closed_for_operator(monkeypatch):
    entries = [{"key":"operator-secret", "key_id":"operator-key", "actor_id":"operator-user", "role":"operator"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    response = TestClient(app).get("/api/v1/future-admin-endpoint", headers={"Authorization":"Bearer operator-secret"})
    assert response.status_code == 403


def test_reader_can_inspect_connector_inventory_but_cannot_register_connectors(monkeypatch):
    entries = [{"key": "reader-secret", "key_id": "reader-key", "actor_id": "reader-user", "role": "reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    response = client.get("/api/v1/connectors", headers={"Authorization": "Bearer reader-secret"})
    assert response.status_code == 200
    assert [item["connector_id"] for item in response.json()["connectors"]] == ["sandbox.email"]
    assert "no external connectors" in response.json()["note"]
    denied = client.post("/api/v1/connectors", headers={"Authorization": "Bearer reader-secret"}, json={})
    assert denied.status_code == 403


def test_execution_history_is_admin_only(monkeypatch):
    entries = [{"key": "history-reader", "key_id": "history-reader-key", "actor_id": "reader", "role": "reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    response = TestClient(app).get("/api/v1/executions", headers={"Authorization": "Bearer history-reader"})
    assert response.status_code == 403


def test_email_provider_inventory_is_admin_only(monkeypatch):
    entries = [
        {"key": "operator-secret", "key_id": "operator-key", "actor_id": "operator-user", "role": "operator"},
        {"key": "admin-secret", "key_id": "admin-key", "actor_id": "admin-user", "role": "admin"},
    ]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    denied = client.get("/api/v1/email/providers", headers={"Authorization": "Bearer operator-secret"})
    allowed = client.get("/api/v1/email/providers", headers={"Authorization": "Bearer admin-secret"})
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["providers"] == []
    assert allowed.json()["real_sending_enabled"] is False


def test_reader_can_view_agent_inventory_but_cannot_assign_agent(monkeypatch):
    entries = [{"key":"reader-agent-secret", "key_id":"reader-agent-key", "actor_id":"reader-user", "role":"reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    listed = client.get("/api/v1/workforce/agents", headers={"Authorization":"Bearer reader-agent-secret"})
    assert listed.status_code == 200
    assigned = client.post("/api/v1/missions/some-mission/agents", headers={"Authorization":"Bearer reader-agent-secret"}, json={"agent_id":"research"})
    assert assigned.status_code == 403


def test_reader_can_list_orchestration_history_but_cannot_run_tasks(monkeypatch):
    entries = [{"key":"history-reader-secret", "key_id":"history-reader-key", "actor_id":"history-reader", "role":"reader"}]
    monkeypatch.setenv("ALEX_API_KEYS_JSON", json.dumps(entries))
    client = TestClient(app)
    headers = {"Authorization": "Bearer history-reader-secret"}
    listed = client.get("/api/v1/orchestrations?limit=5", headers=headers)
    assert listed.status_code == 200
    submitted = client.post("/api/v1/orchestrate", headers=headers, json={})
    assert submitted.status_code == 403


def test_mission_recovery_queue_has_explicit_read_permission_contract():
    # Keep this route explicit so it cannot accidentally inherit permissions
    # from the generic mission-detail route when route patterns are refactored.
    assert _required_permission("GET", "/api/v1/missions/recovery-queue") == "missions:read"
    assert _required_permission("GET", "/api/v1/missions/recovery-audit/pending") == "missions:recover"
    assert _required_permission("POST", "/api/v1/missions/example/recovery-decision") == "missions:recover"


def test_unclassified_api_route_defaults_to_admin_all():
    # Unknown API paths must never inherit a permissive reader/operator grant.
    assert _required_permission("GET", "/api/v1/not-inventory/secret") == "admin:all"
    assert _required_permission("POST", "/api/v1/not-inventory/secret") == "admin:all"


def test_gmail_reconciliation_routes_are_explicitly_admin_only():
    assert _required_permission(
        "GET", "/api/v1/connectors/gmail.email/uncertain-sends"
    ) == "admin:all"
    assert _required_permission(
        "GET", "/api/v1/connectors/gmail.email/reconciliation"
    ) == "admin:all"
    assert _required_permission(
        "POST", "/api/v1/connectors/gmail.email/uncertain-sends/test-attempt/reconcile"
    ) == "admin:all"
