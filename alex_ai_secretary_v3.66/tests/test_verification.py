import pytest
from app.verification.service import VerificationService
from app.workforce.service import WorkforceService
from app.missions.models import MissionContract, MissionService
from app.agents.runtime import AgentRuntime


def test_unverified_checks_remain_uncertain():
    v = VerificationService()
    record = v.verify("task-1", [{"name": "source_check", "passed": False}])
    assert record.verified is False
    assert record.status == "uncertain"


def test_unresolved_questions_block_verification():
    v = VerificationService()
    record = v.verify("task-2", [{"name": "source_check", "passed": True}], unresolved_questions=["deadline unclear"])
    assert record.verified is False


def test_unknown_evidence_reference_rejected():
    v = VerificationService()
    with pytest.raises(ValueError, match="Unknown evidence"):
        v.verify("task-3", [{"name": "source_check", "passed": True}], evidence_ids=["missing"])


def test_agent_result_contains_provenance_and_evidence():
    wf = WorkforceService(); ms = MissionService(workforce=wf); runtime = AgentRuntime(wf, ms)
    ms.create(MissionContract(mission_id="v25", objective="research", allowed_tools=["search_web"], budget=5))
    ms.assign_agent("v25", "research"); ms.start("v25")
    result = runtime.submit("v25", "research", "Find sources", "search_web", budget=1)
    assert result.verification["verified"] is True
    assert result.evidence and result.provenance["agent_id"] == "research"
