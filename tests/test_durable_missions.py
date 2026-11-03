from app.missions.models import MissionContract, MissionService, MissionStatus


def test_missions_survive_service_restart(tmp_path):
    db = tmp_path / "missions.sqlite3"
    first = MissionService(store_path=str(db))
    first.create(MissionContract("durable-1", "Prepare a scholarship shortlist", budget=25))
    first.assign_agent("durable-1", "research")
    first.start("durable-1")
    first.checkpoint("durable-1", "sources_collected", verified=True)
    first.close()

    second = MissionService(store_path=str(db))
    recovered = second.get("durable-1")
    assert recovered.contract.objective == "Prepare a scholarship shortlist"
    assert recovered.assigned_agents == ["research"]
    assert recovered.status == MissionStatus.PAUSED
    assert recovered.checkpoints[-1] == {
        "name": "recovered_after_restart_manual_review_required", "verified": False
    }
    assert any(c["name"] == "sources_collected" for c in recovered.checkpoints)
    second.close()


def test_completed_mission_remains_completed_after_restart(tmp_path):
    db = tmp_path / "missions.sqlite3"
    first = MissionService(store_path=str(db))
    first.create(MissionContract("durable-2", "Prepare a report"))
    first.assign_agent("durable-2", "research")
    first.start("durable-2")
    first.complete("durable-2", {"verified": True, "summary": "done"})
    first.close()

    second = MissionService(store_path=str(db))
    mission = second.get("durable-2")
    assert mission.status == MissionStatus.COMPLETED
    assert mission.results == [{"verified": True, "summary": "done"}]
    second.close()


def test_duplicate_mission_id_rejected(tmp_path):
    service = MissionService(store_path=str(tmp_path / "missions.sqlite3"))
    service.create(MissionContract("same-id", "First mission"))
    try:
        service.create(MissionContract("same-id", "Second mission"))
        assert False, "expected duplicate ID rejection"
    except ValueError as exc:
        assert "already exists" in str(exc)
    service.close()


def test_recovery_decision_keep_paused_is_persisted_and_does_not_resume(tmp_path):
    db = tmp_path / "missions.sqlite3"
    first = MissionService(store_path=str(db))
    first.create(MissionContract("review-keep", "Review uncertain work"))
    first.assign_agent("review-keep", "research")
    first.start("review-keep")
    first.close()

    second = MissionService(store_path=str(db))
    mission = second.get("review-keep")
    assert mission.status == MissionStatus.PAUSED
    result = second.record_recovery_decision("review-keep", "keep_paused", "operator-7", "Need external evidence")
    assert result.status == MissionStatus.PAUSED
    second.close()

    third = MissionService(store_path=str(db))
    recovered = third.get("review-keep")
    assert recovered.status == MissionStatus.PAUSED
    assert recovered.recovery_decisions[0]["decision"] == "keep_paused"
    assert recovered.recovery_decisions[0]["actor_id"] == "operator-7"
    third.close()


def test_recovery_decision_stop_is_persisted_and_terminal(tmp_path):
    db = tmp_path / "missions.sqlite3"
    service = MissionService(store_path=str(db))
    service.create(MissionContract("review-stop", "Review uncertain work"))
    service.assign_agent("review-stop", "research")
    service.start("review-stop")
    service.close()

    service = MissionService(store_path=str(db))
    result = service.record_recovery_decision("review-stop", "stop", "admin-1", "Potential duplicate external action")
    assert result.status == MissionStatus.STOPPED
    service.close()
    service = MissionService(store_path=str(db))
    assert service.get("review-stop").status == MissionStatus.STOPPED
    service.close()


def test_recovery_decision_requires_actor_reason_and_rejects_duplicate(tmp_path):
    service = MissionService(store_path=str(tmp_path / "missions.sqlite3"))
    service.create(MissionContract("review-guard", "Review uncertain work"))
    service.assign_agent("review-guard", "research")
    service.start("review-guard")
    service.close()
    service = MissionService(store_path=str(tmp_path / "missions.sqlite3"))
    for actor, reason in [("", "reason"), ("operator", "  ")]:
        try:
            service.record_recovery_decision("review-guard", "keep_paused", actor, reason)
            assert False, "expected required attribution validation"
        except ValueError:
            pass
    service.record_recovery_decision("review-guard", "keep_paused", "operator", "reviewed")
    try:
        service.record_recovery_decision("review-guard", "stop", "operator", "second decision")
        assert False, "expected duplicate decision rejection"
    except ValueError as exc:
        assert "already recorded" in str(exc)
    service.close()


def test_restart_recovered_mission_cannot_resume_through_start(tmp_path):
    db = tmp_path / "missions.sqlite3"
    service = MissionService(store_path=str(db))
    service.create(MissionContract("no-auto-resume", "Review uncertain work"))
    service.assign_agent("no-auto-resume", "research")
    service.start("no-auto-resume")
    service.close()
    service = MissionService(store_path=str(db))
    try:
        service.start("no-auto-resume")
        assert False, "restart-recovered mission must not resume through start()"
    except ValueError as exc:
        assert "cannot resume" in str(exc)
    assert service.get("no-auto-resume").status == MissionStatus.PAUSED
    service.close()
