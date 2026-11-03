from app.audit import AuditLog


def test_audit_record_with_same_event_id_is_idempotent(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(str(path))
    first = audit.record("example", "actor", "mission", "m-1", {"decision": "stop"}, event_id="stable-id")
    second = audit.record("example", "actor", "mission", "m-1", {"decision": "stop"}, event_id="stable-id")
    assert first is second
    assert len(audit.events) == 1
    assert audit.verify()


def test_audit_record_rejects_conflicting_reuse_of_event_id(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    audit.record("example", "actor", "mission", "m-1", {"decision": "stop"}, event_id="stable-id")
    try:
        audit.record("example", "actor", "mission", "m-1", {"decision": "keep_paused"}, event_id="stable-id")
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("Expected conflicting idempotency key to be rejected")
