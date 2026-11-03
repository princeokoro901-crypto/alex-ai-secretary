from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main
from app.execution_journal import ExecutionJournal

ADMIN = {"actor_id": "admin-test", "key_id": "admin-key", "role": "admin"}
OPERATOR = {"actor_id": "operator-test", "key_id": "operator-key", "role": "operator"}


def _request(actor):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _linked_uncertain_attempt(tmp_path, monkeypatch):
    from testsupport_gmail import make_connected_sender

    _, _, provider, _ = make_connected_sender(tmp_path)
    journal_path = str(tmp_path / "api-execution-journal.sqlite3")
    journal = ExecutionJournal(journal_path)
    execution_id = "exec-linked-api-test"
    journal.begin(execution_id, "permit-api", "mission-api", "action-digest",
                  "email.send_gmail", {"actor_id": "admin-test", "key_id": "admin-key"})
    journal = ExecutionJournal(journal_path)  # recover running execution as uncertain
    reservation = provider.ledger.reserve(provider.account_id, "api-idempotency-key", "payload-hash",
                                          execution_id=execution_id)
    provider.ledger.mark_uncertain(reservation)
    monkeypatch.setattr(main, "gmail_email_provider", provider)
    monkeypatch.setattr(main, "execution_journal", journal)
    return provider, journal, reservation["attempt_id"], execution_id


def test_admin_api_lists_exact_gmail_execution_link_without_mutation(tmp_path, monkeypatch):
    provider, journal, attempt_id, execution_id = _linked_uncertain_attempt(tmp_path, monkeypatch)
    report = main.inspect_gmail_execution_reconciliation(_request(ADMIN), limit=10)
    assert report["counts"]["linked"] == 1
    assert report["attempts"][0]["attempt_id"] == attempt_id
    assert report["attempts"][0]["execution_id"] == execution_id
    assert provider.ledger.get_attempt(provider.account_id, attempt_id)["status"] == "uncertain"
    assert journal.get(execution_id)["status"] == "uncertain"


def test_admin_api_records_decision_but_does_not_unblock_send(tmp_path, monkeypatch):
    provider, journal, attempt_id, execution_id = _linked_uncertain_attempt(tmp_path, monkeypatch)
    result = main.reconcile_gmail_send_attempt(
        attempt_id,
        {"outcome": "confirmed_succeeded", "reason": "Gmail history shows the message",
         "external_reference": "gmail-history:message-123"},
        _request(ADMIN),
    )
    assert result["execution_id"] == execution_id
    assert result["reconciliation"]["outcome"] == "confirmed_succeeded"
    assert provider.ledger.get_attempt(provider.account_id, attempt_id)["status"] == "uncertain"
    assert journal.get(execution_id)["status"] == "uncertain"
    assert len(journal.reconciliations_for(execution_id)) == 1


def test_reconciliation_api_rejects_non_admin_and_unknown_payload_fields(tmp_path, monkeypatch):
    _linked_uncertain_attempt(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as denied:
        main.inspect_gmail_execution_reconciliation(_request(OPERATOR), limit=10)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as invalid:
        main.reconcile_gmail_send_attempt("some-attempt", {"outcome": "still_unknown", "reason": "checked", "secret": "x"}, _request(ADMIN))
    assert invalid.value.status_code == 400
