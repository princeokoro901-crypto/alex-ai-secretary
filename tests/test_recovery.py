import pytest
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService, MissionStatus
from app.agents.runtime import AgentRuntime
from app.recovery.service import RecoveryService, RecoveryAction

def setup():
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    m.create(MissionContract(mission_id="r1", objective="Resilient research", allowed_tools=["search_web"], budget=10))
    m.assign_agent("r1", "research"); m.start("r1")
    return m, r

def test_failed_agent_gets_safe_retry_decision():
    m, r = setup()
    result = r.submit("r1", "research", "Find sources", "search_web", {"simulated_outcome":"failed"}, 1)
    assert result.status == "failed"
    assert m.get("r1").results[-2]["action"] == "retry"

def test_timeout_is_retryable_without_exceeding_budget():
    m, r = setup()
    result = r.submit("r1", "research", "Find sources", "search_web", {"simulated_outcome":"timeout"}, 1)
    assert result.status == "timeout"
    assert m.get("r1").spent_budget == 1
    assert m.get("r1").status == MissionStatus.RUNNING

def test_repeated_failures_escalate_and_pause_mission():
    m, r = setup()
    for i in range(3):
        r.submit("r1", "research", "same recovery operation", "search_web", {"simulated_outcome":"failed"}, 1)
    assert m.get("r1").status == MissionStatus.PAUSED

def test_non_retryable_status_stops_recovery():
    service = RecoveryService()
    record = service.decide("t1", "completed", "already complete")
    assert record.action == RecoveryAction.STOP

def test_unknown_outcome_rejected():
    m, r = setup()
    with pytest.raises(ValueError, match="Unsupported execution outcome"):
        r.submit("r1", "research", "Bad", "search_web", {"simulated_outcome":"crashed"}, 1)
