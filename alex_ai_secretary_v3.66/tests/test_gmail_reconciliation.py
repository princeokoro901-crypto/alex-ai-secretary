import hashlib
import sqlite3

import pytest

from app.connectors.gmail_reconciliation import GmailExecutionReconciliationService
from app.connectors.gmail_sender import GmailSendLedger
from app.execution_journal import ExecutionJournal


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_v328_ledger_migrates_without_guessing_execution_link(tmp_path):
    path = tmp_path / "legacy-ledger.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE gmail_send_ledger (
        account_hash TEXT NOT NULL, key_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
        status TEXT NOT NULL, attempt_id TEXT NOT NULL, provider_message_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(account_hash, key_hash))""")
    db.execute("INSERT INTO gmail_send_ledger VALUES (?,?,?,?,?,?,?,?)",
               (_hash("account-1"), _hash("key-1"), _hash("payload-1"),
                "uncertain", "legacy-attempt-1", None, "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00"))
    db.commit(); db.close()

    ledger = GmailSendLedger(str(path))
    row = ledger.get_attempt("account-1", "legacy-attempt-1")
    assert row["status"] == "uncertain"
    assert row["execution_id"] is None
    assert ledger.unresolved("account-1")[0]["attempt_id"] == "legacy-attempt-1"


def test_legacy_uncertain_attempt_is_not_heuristically_matched(tmp_path):
    ledger = GmailSendLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.reserve("account-1", "key-1", "payload-1")
    # Reopening simulates process recovery and marks an in-progress send uncertain.
    ledger = GmailSendLedger(str(tmp_path / "ledger.sqlite3"))
    journal = ExecutionJournal(str(tmp_path / "journal.sqlite3"))
    journal.begin("unrelated-execution", "permit", "mission", "digest",
                  "email.send_gmail", {"actor_id": "admin", "key_id": "key"})
    ExecutionJournal(str(tmp_path / "journal.sqlite3"))

    service = GmailExecutionReconciliationService(ledger, journal)
    report = service.inspect("account-1")
    assert report["counts"]["unlinked"] == 1
    assert report["attempts"][0]["mapping_status"] == "unlinked"
    with pytest.raises(ValueError, match="no execution link"):
        service.record_decision("account-1", report["attempts"][0]["attempt_id"],
                                "still_unknown", "Evidence remains inconclusive",
                                {"actor_id": "admin", "key_id": "key", "role": "admin"})
    assert journal.reconciliations_for("unrelated-execution") == []


def test_reconciliation_service_flags_wrong_action_type(tmp_path):
    ledger = GmailSendLedger(str(tmp_path / "ledger.sqlite3"))
    reservation = ledger.reserve("account-1", "key-1", "payload-1", execution_id="exec-other")
    ledger.mark_uncertain(reservation)
    journal = ExecutionJournal(str(tmp_path / "journal.sqlite3"))
    journal.begin("exec-other", "permit", "mission", "digest", "files.write",
                  {"actor_id": "admin", "key_id": "key"})
    ExecutionJournal(str(tmp_path / "journal.sqlite3"))

    report = GmailExecutionReconciliationService(ledger, journal).inspect("account-1")
    assert report["counts"]["wrong_execution_type"] == 1
    assert report["attempts"][0]["mapping_status"] == "wrong_execution_type"

