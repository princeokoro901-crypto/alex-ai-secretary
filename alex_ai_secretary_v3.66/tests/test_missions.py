from app.missions.models import MissionContract, MissionService, MissionStatus

def test_mission_requires_agent_before_start():
    s = MissionService()
    s.create(MissionContract("m1", "Research scholarships", budget=100))
    try:
        s.start("m1")
        assert False
    except ValueError as e:
        assert "agent" in str(e).lower()

def test_forbidden_action_stops_mission():
    s = MissionService()
    s.create(MissionContract("m2", "Research only", forbidden_actions=["send_email"], budget=100))
    s.assign_agent("m2", "research_agent")
    s.start("m2")
    try:
        s.propose_action("m2", "send_email to applicant")
        assert False
    except ValueError as e:
        assert "forbids" in str(e).lower()
    assert s.get("m2").status == MissionStatus.STOPPED

def test_budget_guard_stops_mission():
    s = MissionService()
    s.create(MissionContract("m3", "Research", budget=50))
    s.assign_agent("m3", "research_agent")
    s.start("m3")
    s.record_spend("m3", 30)
    try:
        s.record_spend("m3", 25)
        assert False
    except ValueError as e:
        assert "budget" in str(e).lower()
    assert s.get("m3").status == MissionStatus.STOPPED

def test_verified_completion_required():
    s = MissionService()
    s.create(MissionContract("m4", "Prepare report", budget=10))
    s.assign_agent("m4", "research_agent")
    s.start("m4")
    try:
        s.complete("m4", {"verified": False})
        assert False
    except ValueError as e:
        assert "verified" in str(e).lower()
    assert s.complete("m4", {"verified": True}).status == MissionStatus.COMPLETED


def test_mission_rejects_unregistered_agent():
    from app.workforce.service import WorkforceService
    workforce = WorkforceService()
    s = MissionService(workforce=workforce)
    s.create(MissionContract("m5", "Research", budget=10))
    try:
        s.assign_agent("m5", "unknown_agent")
        assert False
    except ValueError as e:
        assert "registered" in str(e).lower()


def test_mission_accepts_registered_agent():
    from app.workforce.service import WorkforceService
    workforce = WorkforceService()
    s = MissionService(workforce=workforce)
    s.create(MissionContract("m6", "Research", budget=10))
    mission = s.assign_agent("m6", "research")
    assert mission.assigned_agents == ["research"]
