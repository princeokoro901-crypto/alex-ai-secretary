import pytest
from app.workforce.service import WorkforceService
from app.missions.models import MissionService
from app.missions.models import MissionContract
from app.handoffs.service import HandoffService


def setup():
    wf = WorkforceService()
    ms = MissionService(wf)
    hs = HandoffService(wf, ms)
    c = MissionContract(mission_id="handoff-m1", objective="research", scope=["research"], allowed_tools=["search_web"], budget=10, verification_required=False)
    m = ms.create(c)
    ms.assign_agent(m.contract.mission_id, "research")
    ms.assign_agent(m.contract.mission_id, "analytics")
    ms.start(m.contract.mission_id)
    return wf, ms, hs, m


def test_handoff_requires_assigned_agents():
    wf, ms, hs, m = setup()
    with pytest.raises(ValueError, match="Both agents"):
        hs.create(m.contract.mission_id, "research", "content", "task-1")


def test_handoff_rejects_self_handoff():
    wf, ms, hs, m = setup()
    with pytest.raises(ValueError, match="different"):
        hs.create(m.contract.mission_id, "research", "research", "task-1")


def test_handoff_preserves_structured_evidence():
    wf, ms, hs, m = setup()
    h = hs.create(m.contract.mission_id, "research", "analytics", "task-1", {"topic":"scholarships"}, [{"url":"https://example.test"}], ["deadline"], {"status":"verified"})
    assert h.findings["topic"] == "scholarships"
    assert h.evidence[0]["url"].startswith("https://")
    assert h.verification["status"] == "verified"


def test_handoff_rejects_unavailable_recipient():
    wf, ms, hs, m = setup()
    wf.agents["analytics"].status = "retired"
    with pytest.raises(ValueError, match="not available"):
        hs.create(m.contract.mission_id, "research", "analytics", "task-1")
