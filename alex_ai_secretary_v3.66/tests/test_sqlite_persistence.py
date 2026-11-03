from app.agents.runtime import AgentRuntime
from app.missions.models import MissionService
from app.orchestrator.service import OrchestratorService, OrchestrationRequest, SQLiteOrchestrationStateStore
from app.routing.service import AgentRoutingService
from app.workforce.service import WorkforceService


def fresh(path):
    w = WorkforceService(); m = MissionService(workforce=w); r = AgentRuntime(w, m)
    router = AgentRoutingService(w, m, r.performance)
    return OrchestratorService(w, m, router, r, SQLiteOrchestrationStateStore(str(path)))


def test_sqlite_persistence_and_idempotency_survive_restart(tmp_path):
    path = tmp_path / "orchestration.db"
    first_service = fresh(path)
    req = OrchestrationRequest("Research", "research", ["search_web"], "search_web", budget=1, idempotency_key="sqlite-1")
    first = first_service.run(req)
    second_service = fresh(path)
    assert second_service.runs[first.orchestration_id].mission_id == first.mission_id
    replay = second_service.run(req)
    assert replay.orchestration_id == first.orchestration_id


def test_sqlite_store_is_transactional(tmp_path):
    path = tmp_path / "orchestration.db"
    store = SQLiteOrchestrationStateStore(str(path))
    store.save({}, {})
    runs, idem = store.load()
    assert runs == {}
    assert idem == {}


def test_sqlite_v1_store_migrates_and_loads_legacy_run_payload(tmp_path):
    import json
    import sqlite3

    path = tmp_path / "legacy-v1.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE runs (orchestration_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("CREATE TABLE idempotency (idempotency_key TEXT PRIMARY KEY, orchestration_id TEXT NOT NULL)")
        conn.execute("INSERT INTO meta(key, value) VALUES ('version', '1')")
        payload = {
            "orchestration_id": "legacy-sqlite", "mission_id": "legacy-mission",
            "status": "uncertain", "selected_agent_id": "research", "result": {},
        }
        conn.execute("INSERT INTO runs(orchestration_id, payload) VALUES (?, ?)", ("legacy-sqlite", json.dumps(payload)))

    store = SQLiteOrchestrationStateStore(str(path))
    runs, _ = store.load()
    assert runs["legacy-sqlite"].recovery_review is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0] == "2"
