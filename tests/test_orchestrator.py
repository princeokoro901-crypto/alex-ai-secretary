import pytest
from fastapi.testclient import TestClient
from app.main import app, workforce, mission_service, agent_runtime, routing_service
from app.orchestrator.service import OrchestratorService, OrchestrationRequest


def fresh():
    from app.workforce.service import WorkforceService
    from app.missions.models import MissionService
    from app.agents.runtime import AgentRuntime
    from app.routing.service import AgentRoutingService
    from app.orchestrator.service import OrchestratorService
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    router = AgentRoutingService(w, m, r.performance)
    o = OrchestratorService(w, m, router, r)
    return w, m, r, o


def test_end_to_end_selects_authorized_agent_and_completes():
    _, m, _, o = fresh()
    out = o.run(OrchestrationRequest("Research scholarships", "research", ["search_web"], "search_web", budget=2))
    assert out.status == "completed"
    assert out.selected_agent_id == "research"
    assert m.get(out.mission_id).status.value == "completed"


def test_orchestrator_does_not_grant_missing_tool():
    _, _, _, o = fresh()
    with pytest.raises(ValueError, match="No authorized agent"):
        o.run(OrchestrationRequest("Send a message", "communication", ["send_message"], "send_message"))


def test_orchestrator_respects_candidate_filter():
    _, _, _, o = fresh()
    with pytest.raises(ValueError, match="No authorized agent"):
        o.run(OrchestrationRequest("Research", "research", ["search_web"], "search_web", candidate_agent_ids=["content"]))


def test_orchestrator_requires_running_supplied_mission():
    from app.missions.models import MissionContract
    _, m, _, o = fresh()
    m.create(MissionContract("m", "Research", allowed_tools=["search_web"], budget=5))
    with pytest.raises(ValueError, match="already be running"):
        o.run(OrchestrationRequest("Research", "research", ["search_web"], "search_web", mission_id="m"))


def test_orchestrator_failure_pauses_without_bypassing_runtime():
    _, m, _, o = fresh()
    out = o.run(OrchestrationRequest("Research", "research", ["search_web"], "search_web", input={"simulated_outcome":"failed"}))
    assert out.status == "failed"
    assert out.recovery_state == "runtime_reported_failure"
    assert m.get(out.mission_id).status.value == "paused"


def test_api_orchestrate_endpoint():
    client = TestClient(app)
    r = client.post("/api/v1/orchestrate", json={"objective":"Research", "task_type":"research", "required_tools":["search_web"], "tool":"search_web", "budget":1})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["selected_agent_id"] == "research"


def test_api_orchestrate_rejects_unauthorized_work():
    client = TestClient(app)
    r = client.post("/api/v1/orchestrate", json={"objective":"Transfer money", "task_type":"finance", "required_tools":["bank_transfer"], "tool":"bank_transfer"})
    assert r.status_code == 400
