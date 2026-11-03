import pytest
from app.agents.memory import ScopedMemoryService
from app.agents.runtime import AgentRuntime
from app.missions.models import MissionContract
from app.workforce.service import WorkforceService


def runtime():
    wf = WorkforceService()
    from app.missions.models import MissionService
    ms = MissionService(workforce=wf)
    rt = AgentRuntime(wf, ms)
    return wf, ms, rt


def mission(ms, agent="research"):
    m = ms.create(MissionContract("memory-test", "research", "test", ["search_web"], [], [], 10, [], True, [], []))
    ms.assign_agent(m.contract.mission_id, agent)
    ms.start(m.contract.mission_id)
    return m


def test_agent_receives_only_its_scoped_memory():
    wf, ms, rt = runtime(); m = mission(ms)
    rt.remember("research", "project", "aero", "assigned_task_context")
    rt.memory.remember("research", "secret", "nope", "assigned_task_context", sensitivity="restricted")
    result = rt.submit(m.contract.mission_id, "research", "find", "search_web", {"max_memory_sensitivity": "normal"})
    assert [x["key"] for x in result.context] == ["project"]


def test_agent_cannot_request_unauthorized_memory_scope():
    wf, ms, rt = runtime(); m = mission(ms)
    with pytest.raises(ValueError, match="outside its authorized scope"):
        rt.submit(m.contract.mission_id, "research", "find", "search_web", {"memory_scopes": ["global_private_memory"]})


def test_agent_cannot_write_outside_memory_scope():
    wf, ms, rt = runtime()
    with pytest.raises(ValueError, match="outside its memory scope"):
        rt.remember("research", "x", "y", "global_private_memory")


def test_agent_cannot_read_another_agent_memory():
    wf, ms, rt = runtime(); m = mission(ms)
    rt.remember("content", "private_note", "content-only", "assigned_task_context")
    result = rt.submit(m.contract.mission_id, "research", "find", "search_web")
    assert result.context == []


def test_memory_belongs_to_agent_owner():
    wf, ms, rt = runtime(); m = mission(ms)
    rt.remember("research", "research_note", "only research", "assigned_task_context")
    result = rt.submit(m.contract.mission_id, "research", "find", "search_web")
    assert result.context[0]["value"] == "only research"


def test_invalid_memory_sensitivity_rejected():
    svc = ScopedMemoryService()
    with pytest.raises(ValueError, match="Unsupported memory sensitivity"):
        svc.remember("a", "k", "v", "assigned_task_context", "secret")
