from app.agents.runtime import AgentRuntime
from app.missions.models import MissionService
from app.orchestrator.service import OrchestratorService, OrchestrationRequest, OrchestrationStateStore
from app.routing.service import AgentRoutingService
from app.workforce.service import WorkforceService


def fresh(path):
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    router = AgentRoutingService(w, m, r.performance)
    return OrchestratorService(w, m, router, r, OrchestrationStateStore(str(path)))


def test_orchestration_and_idempotency_survive_restart(tmp_path):
    path = tmp_path / "orchestration.json"
    first_service = fresh(path)
    req = OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="persist-1")
    first = first_service.run(req)

    second_service = fresh(path)
    assert second_service.runs[first.orchestration_id].mission_id == first.mission_id
    replay = second_service.run(req)
    assert replay.orchestration_id == first.orchestration_id
    assert len(second_service.runs) == 1
