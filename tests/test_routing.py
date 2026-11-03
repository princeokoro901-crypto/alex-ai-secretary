import pytest
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService
from app.agents.runtime import AgentRuntime
from app.routing.service import AgentRoutingService
from app.workforce.performance import PerformanceEvent


def setup():
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    m.create(MissionContract(mission_id="m1", objective="Research", allowed_tools=["search_web"], budget=20))
    m.assign_agent("m1", "research"); m.start("m1")
    router = AgentRoutingService(w, m, r.performance)
    return w, m, r, router


def test_route_requires_authorized_capabilities():
    w, _, _, router = setup()
    recs = router.route("research", ["search_web"])
    assert [x.agent_id for x in recs] == ["research"]


def test_route_respects_mission_assignment_and_allowed_tools():
    _, _, _, router = setup()
    assert router.route("research", ["search_web"], mission_id="m1")[0].agent_id == "research"
    assert router.route("research", ["send_email"], mission_id="m1") == []


def test_performance_breaks_ties_without_granting_authority():
    w, _, r, router = setup()
    r.performance.record(PerformanceEvent("research", "t1", "search_web", "completed", True, confidence=1.0))
    # Candidate restriction still applies; content's injected history cannot grant search_web.
    r.performance.record(PerformanceEvent("content", "t2", "search_web", "completed", True, confidence=1.0))
    recs = router.route("research", ["search_web"], candidate_agent_ids=["content", "research"])
    assert [x.agent_id for x in recs] == ["research"]


def test_route_rejects_unknown_candidate():
    _, _, _, router = setup()
    with pytest.raises(ValueError, match="Unknown candidate"):
        router.route("research", ["search_web"], candidate_agent_ids=["ghost"])


def test_route_rejects_empty_required_tools():
    _, _, _, router = setup()
    with pytest.raises(ValueError, match="required tool"):
        router.route("research", [])
