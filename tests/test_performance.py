import pytest
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService
from app.agents.runtime import AgentRuntime


def setup():
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    m.create(MissionContract(mission_id="m1", objective="Research", allowed_tools=["search_web"], budget=20))
    m.assign_agent("m1", "research"); m.start("m1")
    return w, m, r


def test_performance_records_success_and_verification():
    _, _, r = setup()
    r.submit("m1", "research", "Find sources", "search_web", budget=2, idempotency_key="p1")
    p = r.performance.profile("research")
    assert p.tasks_completed == 1
    assert p.verification_rate == 1.0
    assert p.success_rate == 1.0


def test_failed_and_uncertain_results_are_tracked_separately():
    _, _, r = setup()
    r.submit("m1", "research", "Fail", "search_web", input_data={"simulated_outcome":"failed", "recovery_key":"f"})
    r.submit("m1", "research", "Uncertain", "search_web", input_data={"simulated_outcome":"uncertain", "recovery_key":"u"})
    p = r.performance.profile("research")
    assert p.tasks_failed == 1
    assert p.tasks_uncertain == 1
    assert p.success_rate == 0.0


def test_recommendation_uses_observed_tool_performance():
    w, _, r = setup()
    # Content is not authorized for search_web and therefore must never be recommended for it.
    for _ in range(2):
        r.submit("m1", "research", "Find sources", "search_web", budget=1)
    recs = r.performance.recommend(list(w.agents.values()), "search_web")
    assert [x["agent_id"] for x in recs] == ["research"]
    assert recs[0]["score"] > 0


def test_performance_does_not_grant_tool_authority():
    w, _, r = setup()
    r.performance.record(__import__('app.workforce.performance', fromlist=['PerformanceEvent']).PerformanceEvent(
        agent_id="content", task_id="t", tool="search_web", status="completed", verified=True, confidence=1.0
    ))
    # Candidate filter still requires the real workforce tool permission.
    recs = r.performance.recommend([w.agents["content"]], "search_web")
    assert recs == []


def test_candidate_filter_limits_recommendations():
    w, _, r = setup()
    r.performance.record(__import__('app.workforce.performance', fromlist=['PerformanceEvent']).PerformanceEvent(
        agent_id="research", task_id="t", tool="search_web", status="completed", verified=True, confidence=1.0
    ))
    assert r.performance.recommend([w.agents["research"]], "search_web")[0]["agent_id"] == "research"
