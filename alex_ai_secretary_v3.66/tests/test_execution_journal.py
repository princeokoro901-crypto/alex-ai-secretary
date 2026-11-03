import pytest
from app.execution_journal import ExecutionJournal


def test_journal_persists_terminal_outcome(tmp_path):
    path = str(tmp_path / 'executions.sqlite3')
    journal = ExecutionJournal(path)
    journal.begin('e1', 'p1', 'm1', 'digest', 'report', {'actor_id': 'a', 'key_id': 'k'})
    journal.finish('e1', 'succeeded', result={'ok': True})
    reopened = ExecutionJournal(path)
    row = reopened.get('e1')
    assert row['status'] == 'succeeded'
    assert row['result'] == {'ok': True}
    assert row['actor_id'] == 'a'


def test_interrupted_running_execution_becomes_uncertain_not_retryable(tmp_path):
    path = str(tmp_path / 'executions.sqlite3')
    first = ExecutionJournal(path)
    first.begin('e2', 'p2', 'm2', 'digest', 'email', {'actor_id': 'a'})
    restarted = ExecutionJournal(path)
    row = restarted.get('e2')
    assert row['status'] == 'uncertain'
    assert 'reconciliation' in row['error']
    assert len(restarted.list()) == 1


def test_admin_reconciliation_is_append_only_and_does_not_change_execution_status(tmp_path):
    journal = ExecutionJournal(str(tmp_path / 'executions.sqlite3'))
    journal.begin('e3', 'p3', 'm3', 'digest', 'payment', {'actor_id': 'worker', 'key_id': 'kw'})
    ExecutionJournal(str(tmp_path / 'executions.sqlite3'))  # simulate restart

    record = journal.record_reconciliation(
        'e3', 'confirmed_failed', 'Provider dashboard confirms failure',
        {'actor_id': 'admin-1', 'key_id': 'ka', 'role': 'admin'}, 'provider-ref-123')
    assert record['outcome'] == 'confirmed_failed'
    assert record['actor_id'] == 'admin-1'
    assert record['key_id'] == 'ka'
    assert journal.get('e3')['status'] == 'uncertain'
    assert journal.reconciliations_for('e3') == [record]


def test_reconciliation_rejects_non_admin_and_empty_reason(tmp_path):
    journal = ExecutionJournal(str(tmp_path / 'executions.sqlite3'))
    journal.begin('e4', 'p4', 'm4', 'digest', 'email', {'actor_id': 'worker'})
    ExecutionJournal(str(tmp_path / 'executions.sqlite3'))
    try:
        journal.record_reconciliation('e4', 'still_unknown', 'checked',
                                      {'actor_id': 'operator', 'role': 'operator'})
        assert False, 'non-admin must be rejected'
    except PermissionError:
        pass
    try:
        journal.record_reconciliation('e4', 'still_unknown', ' ',
                                      {'actor_id': 'admin', 'role': 'admin'})
        assert False, 'empty reason must be rejected'
    except ValueError:
        pass
    assert journal.reconciliations_for('e4') == []


def test_reconciliation_requires_existing_uncertain_execution(tmp_path):
    journal = ExecutionJournal(str(tmp_path / 'executions.sqlite3'))
    actor = {'actor_id': 'admin', 'role': 'admin'}
    try:
        journal.record_reconciliation('missing', 'still_unknown', 'looked up', actor)
        assert False, 'missing execution must be rejected'
    except LookupError:
        pass
    journal.begin('e5', 'p5', 'm5', 'digest', 'report', {'actor_id': 'worker'})
    journal.finish('e5', 'succeeded', result={'ok': True})
    try:
        journal.record_reconciliation('e5', 'confirmed_succeeded', 'verified', actor)
        assert False, 'terminal execution must not be reconciled'
    except ValueError:
        pass


def test_execution_journal_history_can_be_bounded_and_filtered(tmp_path):
    from app.execution_journal import ExecutionJournal
    journal = ExecutionJournal(str(tmp_path / "history.sqlite3"))
    journal.begin("exec-a", "permit-a", "mission-a", "digest-a", "tool.demo", {"actor_id": "a", "key_id": "k"})
    journal.finish("exec-a", "succeeded", result={"ok": True})
    journal.begin("exec-b", "permit-b", "mission-b", "digest-b", "tool.demo", {"actor_id": "b", "key_id": "k"})
    journal.finish("exec-b", "failed", error="simulated failure")
    assert len(journal.list(limit=1)) == 1
    assert [row["execution_id"] for row in journal.list(status="failed")] == ["exec-b"]
    assert [row["execution_id"] for row in journal.list(status="succeeded")] == ["exec-a"]
    with pytest.raises(ValueError):
        journal.list(status="pending")
    with pytest.raises(ValueError):
        journal.list(limit=501)


def test_reconciliation_rejects_invalid_and_duplicate_or_conflicting_decisions(tmp_path):
    import pytest
    journal = ExecutionJournal(str(tmp_path / "reconcile-contract.sqlite3"))
    journal.begin("reconcile-e1", "p1", "m1", "digest", "local.test", {"actor_id": "worker", "key_id": "wk"})
    # Simulate a process interruption: running execution becomes uncertain.
    journal = ExecutionJournal(str(tmp_path / "reconcile-contract.sqlite3"))
    admin = {"actor_id": "admin", "key_id": "ak", "role": "admin"}

    with pytest.raises(ValueError, match="Outcome must be"):
        journal.record_reconciliation("reconcile-e1", "succeeded", "checked", admin)
    assert journal.reconciliations_for("reconcile-e1") == []

    first = journal.record_reconciliation("reconcile-e1", "confirmed_failed", "Provider record confirms failure", admin)
    assert first["outcome"] == "confirmed_failed"
    assert journal.get("reconcile-e1")["status"] == "uncertain"

    # Reconciliation is append-only evidence; the same execution cannot receive a
    # second, conflicting decision because it is no longer in an uncertain state.
    with pytest.raises(ValueError, match="conclusive reconciliation decision"):
        journal.record_reconciliation("reconcile-e1", "confirmed_succeeded", "Conflicting later report", admin)
    assert journal.reconciliations_for("reconcile-e1") == [first]
    assert journal.get("reconcile-e1")["status"] == "uncertain"
