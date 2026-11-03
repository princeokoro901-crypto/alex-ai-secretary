from app.audit import AuditLog

def test_audit_jsonl_roundtrip(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    event = log.record('orchestration_completed', 'system', 'orchestration', 'o1', {'status':'completed'})
    loaded = AuditLog(str(path))
    assert loaded.list(resource_id='o1')[0].event_id == event.event_id


def test_audit_hash_chain_and_tamper_recovery(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    first = log.record('one', 'system', 'x', '1')
    log.record('two', 'system', 'x', '2')
    assert first.event_hash
    assert log.verify()
    text = path.read_text()
    path.write_text(text.replace('"event_type": "two"', '"event_type": "tampered"'))
    loaded = AuditLog(str(path))
    assert loaded.events == []
    assert loaded.recovery_path and loaded.recovery_path.endswith('.corrupt')


def test_audit_redacts_secret_fields_recursively(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    event = log.record('credential_rotated', 'operator:key-17', 'credential', 'key-17', {
        'key_id': 'key-17', 'api_key': 'never-store-this',
        'nested': {'Authorization': 'Bearer private', 'result': 'ok'},
        'items': [{'client_secret': 'also-private'}],
    })
    raw = path.read_text()
    assert 'never-store-this' not in raw
    assert 'Bearer private' not in raw
    assert 'also-private' not in raw
    assert event.details['api_key'] == '[REDACTED]'
    assert event.details['nested']['Authorization'] == '[REDACTED]'
    assert event.details['nested']['result'] == 'ok'
    assert log.verify()
    assert AuditLog(str(path)).verify()


def test_failed_durable_write_does_not_advance_memory_chain(tmp_path, monkeypatch):
    import app.audit as audit_module
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    def fail_fsync(_fd):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(audit_module.os, 'fsync', fail_fsync)
    try:
        log.record('should_fail', 'system', 'test', '1')
        assert False, 'expected durable write to fail'
    except OSError:
        pass
    assert log.events == []
    assert log.verify()


def test_incomplete_final_line_is_quarantined_and_valid_history_recovered(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    first = log.record('one', 'system', 'x', '1')
    with path.open('ab') as f:
        f.write(b'{"event_id":"truncated')
    loaded = AuditLog(str(path))
    assert [e.event_id for e in loaded.events] == [first.event_id]
    assert loaded.verify()
    assert (tmp_path / 'audit.jsonl.partial').read_bytes() == b'{"event_id":"truncated'
    assert path.read_bytes().endswith(b'\n')


def test_malformed_terminated_record_still_triggers_quarantine(tmp_path):
    path = tmp_path / 'audit.jsonl'
    log = AuditLog(str(path))
    log.record('one', 'system', 'x', '1')
    with path.open('ab') as f:
        f.write(b'{"broken":\n')
    loaded = AuditLog(str(path))
    assert loaded.events == []
    assert loaded.recovery_path and loaded.recovery_path.endswith('.corrupt')
