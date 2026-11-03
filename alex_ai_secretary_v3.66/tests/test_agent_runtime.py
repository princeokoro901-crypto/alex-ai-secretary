import pytest
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService
from app.agents.runtime import AgentRuntime


def setup():
    w = WorkforceService(); m = MissionService(workforce=w)
    r = AgentRuntime(w, m)
    mission = m.create(MissionContract(mission_id="m1", objective="Research launch systems", allowed_tools=["search_web"], budget=10))
    m.assign_agent("m1", "research"); m.start("m1")
    return w, m, r

def test_authorized_agent_executes():
    _, _, r = setup()
    result = r.submit("m1", "research", "Find sources", "search_web", budget=2)
    assert result.status == "completed" and result.verification["verified"]

def test_unassigned_agent_cannot_execute():
    _, _, r = setup()
    with pytest.raises(ValueError, match="not assigned"):
        r.submit("m1", "content", "Write", "write")

def test_agent_cannot_use_unlisted_tool():
    _, _, r = setup()
    with pytest.raises(ValueError, match="not authorized"):
        r.submit("m1", "research", "Write", "send_email")

def test_mission_tool_boundary_is_enforced():
    _, _, r = setup()
    with pytest.raises(ValueError, match="Mission does not allow"):
        r.submit("m1", "research", "Extract", "extract_sources")

def test_budget_excess_stops_mission():
    _, m, r = setup()
    with pytest.raises(ValueError, match="budget exceeded"):
        r.submit("m1", "research", "Large task", "search_web", budget=11)
    assert m.get("m1").status.value == "stopped"

def test_non_running_mission_cannot_execute():
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    m.create(MissionContract(mission_id="m2", objective="Research", allowed_tools=["search_web"], budget=10)); m.assign_agent("m2", "research")
    with pytest.raises(ValueError, match="must be running"):
        r.submit("m2", "research", "Find", "search_web")


def test_duplicate_idempotency_returns_same_result():
    _, _, r = setup()
    first = r.submit("m1", "research", "Find sources", "search_web", budget=2, idempotency_key="abc")
    second = r.submit("m1", "research", "Find sources", "search_web", budget=2, idempotency_key="abc")
    assert first.task_id == second.task_id

