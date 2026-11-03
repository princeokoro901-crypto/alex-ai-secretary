from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main
from app.execution_journal import ExecutionJournal

ADMIN = {"actor_id": "admin-v362", "key_id": "admin-key-v362", "role": "admin"}
READER = {"actor_id": "reader-v362", "key_id": "reader-key-v362", "role": "reader"}


def request(actor):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


class FaultInjectingAudit:
    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.events = {}

    def record(self, event_type, actor, resource_type, resource_id, details=None, event_id=None):
        if self.fail_first:
            self.fail_first = False
            raise OSError("simulated audit storage outage")
        event_id = event_id or f"event-{len(self.events) + 1}"
        value = (event_type, actor, resource_type, resource_id, details or {})
        if event_id in self.events and self.events[event_id] != value:
            raise ValueError("conflicting event ID")
        self.events[event_id] = value
        return value


def uncertain_journal(path):
    journal = ExecutionJournal(str(path))
    journal.begin("exec-v362", "permit-v362", "mission-v362", "digest", "safe.test", {"actor_id": "worker"})
    return ExecutionJournal(str(path))


def test_audit_failure_leaves_durable_pending_record_and_retry_completes(tmp_path, monkeypatch):
    journal = uncertain_journal(tmp_path / "journal.sqlite3")
    audit = FaultInjectingAudit(fail_first=True)
    monkeypatch.setattr(main, "execution_journal", journal)
    monkeypatch.setattr(main, "audit_log", audit)

    with pytest.raises(HTTPException) as failed:
        main.reconcile_execution("exec-v362", {"outcome": "confirmed_failed", "reason": "verified from provider"}, request(ADMIN))
    assert failed.value.status_code == 503
    pending = journal.pending_reconciliation_audits()
    assert len(pending) == 1
    record = pending[0]
    assert record["audit_status"] == "pending"
    assert journal.get("exec-v362")["status"] == "uncertain"

    visible = main.pending_execution_reconciliation_audits(request(ADMIN), limit=100)
    assert visible["count"] == 1
    assert visible["pending"][0]["reconciliation_id"] == record["reconciliation_id"]

    retried = main.retry_execution_reconciliation_audit(record["reconciliation_id"], request(ADMIN))
    assert retried == {"reconciliation_id": record["reconciliation_id"], "audit_status": "recorded", "already_recorded": False}
    assert journal.pending_reconciliation_audits() == []
    assert journal.reconciliations_for("exec-v362")[0]["audit_status"] == "recorded"
    assert len(audit.events) == 1
    assert journal.get("exec-v362")["status"] == "uncertain"

    again = main.retry_execution_reconciliation_audit(record["reconciliation_id"], request(ADMIN))
    assert again["already_recorded"] is True
    assert len(audit.events) == 1


def test_audit_success_followed_by_status_update_failure_retries_idempotently(tmp_path, monkeypatch):
    journal = uncertain_journal(tmp_path / "journal.sqlite3")
    audit = FaultInjectingAudit()
    monkeypatch.setattr(main, "execution_journal", journal)
    monkeypatch.setattr(main, "audit_log", audit)
    original_mark = journal.mark_reconciliation_audit_status
    fail_once = {"enabled": True}

    def mark_with_one_failure(reconciliation_id, status):
        if fail_once["enabled"]:
            fail_once["enabled"] = False
            raise OSError("simulated journal status update outage")
        return original_mark(reconciliation_id, status)

    monkeypatch.setattr(journal, "mark_reconciliation_audit_status", mark_with_one_failure)
    with pytest.raises(HTTPException) as failed:
        main.reconcile_execution("exec-v362", {"outcome": "still_unknown", "reason": "provider result remains ambiguous"}, request(ADMIN))
    assert failed.value.status_code == 503
    assert len(audit.events) == 1
    record = journal.pending_reconciliation_audits()[0]

    retried = main.retry_execution_reconciliation_audit(record["reconciliation_id"], request(ADMIN))
    assert retried["audit_status"] == "recorded"
    assert len(audit.events) == 1  # stable event ID prevents duplicate audit rows


def test_pending_audit_queue_is_admin_only_and_unknown_retry_is_404(tmp_path, monkeypatch):
    journal = uncertain_journal(tmp_path / "journal.sqlite3")
    monkeypatch.setattr(main, "execution_journal", journal)
    with pytest.raises(HTTPException) as denied:
        main.pending_execution_reconciliation_audits(request(READER), limit=100)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        main.retry_execution_reconciliation_audit(9999, request(ADMIN))
    assert missing.value.status_code == 404


def test_existing_journal_schema_is_upgraded_without_losing_reconciliations(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE executions (
            execution_id TEXT PRIMARY KEY, permit_id TEXT NOT NULL, mission_id TEXT NOT NULL,
            action_digest TEXT NOT NULL, action_type TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, actor_id TEXT, key_id TEXT,
            result_json TEXT, error TEXT)""")
        db.execute("""CREATE TABLE execution_reconciliations (
            reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
            actor_id TEXT NOT NULL, key_id TEXT, external_reference TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id))""")
        db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("legacy-exec", "p", "m", "d", "safe.test", "uncertain", "now", "now", "worker", "k", None, "uncertain"))
        db.execute("INSERT INTO execution_reconciliations (execution_id,outcome,reason,actor_id,key_id,external_reference,created_at) VALUES (?,?,?,?,?,?,?)",
                   ("legacy-exec", "still_unknown", "old decision", "admin", "ak", None, "now"))
    journal = ExecutionJournal(path)
    pending = journal.pending_reconciliation_audits()
    assert len(pending) == 1
    assert pending[0]["reconciliation_id"] == 1
    assert pending[0]["audit_event_id"] == "execution-reconciliation-1"
    assert journal.reconciliations_for("legacy-exec")[0]["reason"] == "old decision"
