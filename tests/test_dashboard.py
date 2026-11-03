from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_dashboard_is_served_with_no_store_and_security_headers():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "Alex Core" in response.text


def test_dashboard_assets_are_served_from_the_same_origin():
    script = client.get("/dashboard-assets/dashboard.js")
    stylesheet = client.get("/dashboard-assets/dashboard.css")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert "/api/v1/connectors" in script.text
    assert "/api/v1/approvals?status=pending" in script.text


def test_dashboard_uses_session_storage_not_persistent_local_storage():
    script = client.get("/dashboard-assets/dashboard.js").text
    assert "sessionStorage" in script
    assert "localStorage" not in script
    assert "innerHTML" not in script


def test_dashboard_has_no_inline_script_or_third_party_assets():
    response = client.get("/")
    html = response.text
    assert '<script src="/dashboard-assets/dashboard.js" defer></script>' in html
    assert "<script>" not in html
    assert "https://" not in html
    assert "http://" not in html


def test_health_reports_v340():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "3.66"


def test_mission_workspace_is_present_and_has_safe_result_container():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'id="mission-create-form"' in html
    assert 'id="mission-lookup-form"' in html
    assert 'aria-live="polite"' in html
    assert 'id="mission-result"' in html
    assert '/api/v1/missions' in script
    assert 'textContent = JSON.stringify(data, null, 2)' in script
    assert 'innerHTML' not in script
    assert 'verification_required: true' in script
    assert 'has not been assigned, started, or executed' in script


def test_mission_workspace_supports_assignment_without_execution_shortcuts():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'Create or inspect a mission' in html
    assert 'id="agent-select"' in html
    assert 'id="assign-agent-button"' in html
    assert '/api/v1/workforce/agents' in script
    assert '/agents`, { agent_id: agentId }' in script
    assert 'Mission has not been started or executed.' in script
    assert 'Approve mission' not in html
    assert 'Execute mission' not in html


def test_mission_list_endpoint_returns_read_only_collection():
    from app.main import mission_service
    from app.missions.models import MissionContract

    mission_id = "dashboard-list-test-mission"
    mission_service.create(MissionContract(mission_id=mission_id, objective="List endpoint test"))
    response = client.get("/api/v1/missions")
    assert response.status_code == 200
    body = response.json()
    assert "missions" in body and isinstance(body["missions"], list)
    assert any(item["contract"]["mission_id"] == mission_id for item in body["missions"])


def test_mission_list_ui_uses_safe_dom_rendering():
    html = client.get("/").text
    script = client.get("/dashboard-assets/dashboard.js").text
    assert 'id="mission-list-refresh"' in html
    assert 'id="mission-list"' in html
    assert 'apiGet("/api/v1/missions")' in script
    assert "textContent = contract.objective" in script
    assert "innerHTML" not in script


def test_assignment_ui_is_rendered_and_uses_registered_agent_api():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'Assign a registered agent' in html
    assert 'does not start the mission' in html
    assert 'apiGet("/api/v1/workforce/agents")' in script
    assert 'textContent = JSON.stringify(data, null, 2)' in script


def test_orchestration_workspace_requires_explicit_confirmation_and_reports_simulation_scope():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'id="orchestration-form"' in html
    assert 'id="orchestration-confirm"' in html
    assert 'id="orchestration-run-button"' in html
    assert 'does not perform live web searches' in html
    assert 'globalThis.confirm(' in script
    assert 'apiWrite("/api/v1/orchestrate", payload)' in script
    assert 'verification_required: true' in script
    assert 'innerHTML' not in script


def test_orchestration_form_validates_tools_budget_and_json_before_submission():
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'requiredTools.includes(tool)' in script
    assert 'budget < 0' in script
    assert 'Task input must be valid JSON.' in script
    assert 'Task input must be a JSON object.' in script
    assert 'orchestration-confirm' in script


def test_orchestration_history_workspace_is_read_only_and_safe():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'id="orchestration-history-refresh"' in html
    assert 'id="orchestration-history-list"' in html
    assert 'does not automatically retry failed or uncertain tasks' in html
    assert 'apiGet("/api/v1/orchestrations?limit=25")' in script
    assert 'Inspect run' in script
    assert 'Recovery: ${run.recovery_state || \"none\"}' in script
    assert 'lastPayloadIdempotencyKey' in script
    assert 'no retry was started.' in script
    assert 'innerHTML' not in script


def test_uncertain_run_recovery_review_ui_is_explicit_and_never_retries():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'immutable acknowledgement or escalation decision' in html
    assert 'acknowledged_no_retry' in script
    assert 'escalated_for_manual_investigation' in script
    assert '/recovery-review' in script
    assert 'Task status is unchanged; no retry was started.' in script
    assert 'innerHTML' not in script


def test_pending_recovery_audit_panel_is_operator_visible_and_uses_safe_retry_api():
    html = client.get('/').text
    script = client.get('/dashboard-assets/dashboard.js').text
    assert 'id="mission-audit-pending-refresh"' in html
    assert 'id="mission-audit-pending-list"' in html
    assert 'id="mission-audit-pending-count"' in html
    assert 'Retry audit delivery' in script
    assert 'apiGet("/api/v1/missions/recovery-audit/pending")' in script
    assert '/recovery-audit/retry' in script
    assert 'Mission state was not changed.' in script
    assert 'Audit retry failed for ${item.mission_id}' in script
    assert 'The decision remains saved; refresh and retry when storage is available.' in script
    assert 'innerHTML' not in script
