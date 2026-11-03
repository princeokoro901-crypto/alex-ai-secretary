import json
from app.orchestrator.service import OrchestrationStateStore, OrchestrationResult


def test_v1_state_is_migrated(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "runs": {}, "idempotency": {}}), encoding="utf-8")
    store = OrchestrationStateStore(str(path))
    assert store.load() == ({}, {})
    store.save({}, {})
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 3


def test_corrupt_state_is_quarantined_and_service_can_start(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    store = OrchestrationStateStore(str(path))
    assert store.load() == ({}, {})
    assert not path.exists()
    assert (tmp_path / "state.json.corrupt").exists()


def test_v2_state_is_migrated_with_default_recovery_review(tmp_path):
    path = tmp_path / "state-v2.json"
    old_record = {
        "orchestration_id": "legacy-v2", "mission_id": "legacy-mission",
        "status": "uncertain", "selected_agent_id": "research", "result": {},
        "route_reason": [], "created_at": "", "updated_at": "",
        "recovery_state": "interrupted_after_restart", "request_metadata": {},
    }
    path.write_text(json.dumps({"version": 2, "runs": {"legacy-v2": old_record}, "idempotency": {}}), encoding="utf-8")
    store = OrchestrationStateStore(str(path))
    runs, _ = store.load()
    assert runs["legacy-v2"].recovery_review is None
    store.save(runs, {})
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 3
