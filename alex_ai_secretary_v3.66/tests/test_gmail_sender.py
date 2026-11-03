import base64
from email import message_from_bytes
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.approvals import ApprovalService
from app.connectors.email_provider import EmailDeliveryUncertain, EmailProviderError, EmailProviderRegistry
from app.connectors.gmail_sender import GoogleGmailEmailProvider
from app.connectors.google_oauth import GOOGLE_REQUIRED_SCOPES, GoogleOAuthAdapter
from app.connectors.oauth_security import OAuthAuthorizationService, OAuthCredentialVault
from app.connectors.registry import ConnectorManifest, ConnectorRegistry
from app.execution import ExecutionGateway
from app.execution_journal import ExecutionJournal

OWNER = {"actor_id": "account-owner", "key_id": "owner-key", "role": "operator"}
ADMIN = {"actor_id": "admin-user", "key_id": "admin-key", "role": "admin"}
PAYLOAD = {"to": "recipient@example.com", "subject": "Approved report", "body": "Hello from Alex.", "idempotency_key": "report-001"}


class FakeGoogleTransport:
    def __init__(self, send_error=None):
        self.send_error = send_error
        self.gmail_calls = []
        self.token_calls = []

    def post_form(self, url, form):
        self.token_calls.append((url, dict(form)))
        if form.get("grant_type") == "refresh_token":
            return {"access_token": "ACCESS_REFRESHED", "token_type": "Bearer", "expires_in": 7200}
        return {"access_token": "ACCESS_SECRET", "refresh_token": "REFRESH_SECRET", "token_type": "Bearer",
                "expires_in": 3600, "scope": " ".join(GOOGLE_REQUIRED_SCOPES)}

    def get_bearer_json(self, url, access_token):
        return {"sub": "google-sub-123", "email": "sender@example.com", "email_verified": True}

    def revoke_token(self, token):
        return None

    def send_gmail_message(self, access_token, raw_message):
        self.gmail_calls.append((access_token, raw_message))
        if self.send_error:
            raise self.send_error
        return {"id": "gmail-message-123", "threadId": "thread-123"}


def make_connected_sender(tmp_path, *, send_error=None):
    key = Fernet.generate_key()
    transport = FakeGoogleTransport(send_error=send_error)
    auth = OAuthAuthorizationService(tmp_path / "state.sqlite3", key,
                                     {"gmail": "https://app.example.test/oauth/google/callback"}, clock=lambda: 1000.0)
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", key, clock=lambda: 1000.0)
    oauth = GoogleOAuthAdapter(client_id="client-id", client_secret="client-secret",
                               authorization_service=auth, credential_vault=vault,
                               enabled=True, transport=transport, clock=lambda: 1000.0)
    started = oauth.begin(OWNER["actor_id"], OWNER["key_id"])
    oauth.complete(code="one-time-code", state=started["state"], actor_id=OWNER["actor_id"], key_id=OWNER["key_id"])
    provider = GoogleGmailEmailProvider(oauth, owner_actor_id=OWNER["actor_id"],
                                        account_id="google-sub-123", ledger_path=str(tmp_path / "gmail-ledger.sqlite3"))
    providers = EmailProviderRegistry(real_sending_enabled=True, allowed_provider_ids=("gmail",))
    providers.register("gmail", provider)
    return oauth, transport, provider, providers


def test_gmail_sender_submits_mime_message_and_replays_success_without_second_send(tmp_path):
    _, transport, provider, registry = make_connected_sender(tmp_path)
    action = registry.build_action("gmail", PAYLOAD)
    result = registry.send("gmail", PAYLOAD, action)
    assert result["status"] == "sent"
    assert result["provider_message_id"] == "gmail-message-123"
    assert result["duplicate_replay"] is False
    assert len(transport.gmail_calls) == 1
    token, raw = transport.gmail_calls[0]
    assert token == "ACCESS_SECRET"
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    parsed = message_from_bytes(decoded)
    assert parsed["To"] == PAYLOAD["to"]
    assert parsed["Subject"] == PAYLOAD["subject"]
    assert "Hello from Alex." in parsed.get_payload(decode=True).decode()
    replay = registry.send("gmail", PAYLOAD, action)
    assert replay["duplicate_replay"] is True
    assert len(transport.gmail_calls) == 1
    assert provider.ledger.status_for_key("google-sub-123", PAYLOAD["idempotency_key"])["status"] == "sent"


def test_same_gmail_idempotency_key_cannot_be_reused_for_different_content(tmp_path):
    _, transport, _, registry = make_connected_sender(tmp_path)
    first_action = registry.build_action("gmail", PAYLOAD)
    registry.send("gmail", PAYLOAD, first_action)
    changed = {**PAYLOAD, "body": "Changed after the original send."}
    changed_action = registry.build_action("gmail", changed)
    with pytest.raises(EmailProviderError, match="different Gmail message"):
        registry.send("gmail", changed, changed_action)
    assert len(transport.gmail_calls) == 1


def test_ambiguous_gmail_send_is_journaled_uncertain_and_never_retried(tmp_path):
    _, transport, provider, provider_registry = make_connected_sender(tmp_path, send_error=RuntimeError("provider body secret"))
    approvals = ApprovalService(str(tmp_path / "approvals.json"))
    journal = ExecutionJournal(str(tmp_path / "executions.sqlite3"))
    gateway = ExecutionGateway(approvals, journal=journal)
    connectors = ConnectorRegistry(gateway)
    connectors.register(
        ConnectorManifest(
            connector_id="gmail.email", name="Gmail Email", description="Approved Gmail send",
            action_type="email.send_gmail",
            input_fields={"to": "string", "subject": "string", "body": "string", "idempotency_key": "string"},
            required_fields=("to", "subject", "body", "idempotency_key"), allowed_roles=("admin",),
        ),
        lambda payload, approved_action, context: provider_registry.send(
            "gmail", payload, approved_action, execution_context=context),
        action_builder=lambda payload: provider_registry.build_action("gmail", payload),
        action_aware_handler=True,
        execution_context_handler=True,
    )
    action = provider_registry.build_action("gmail", PAYLOAD)
    approval = approvals.create("mission-gmail-1", action, OWNER)
    approvals.decide(approval.approval_id, "approved", ADMIN, "Approved test delivery")
    permit = approvals.issue_execution_permit(approval.approval_id, "mission-gmail-1", action, ADMIN)
    result = connectors.execute(connector_id="gmail.email", permit_id=permit.execution_permit_id,
                                mission_id="mission-gmail-1", action=action, payload=PAYLOAD, actor=ADMIN)
    assert result.status == "uncertain"
    assert journal.get(result.execution_id)["status"] == "uncertain"
    assert "provider body secret" not in repr(result)
    assert len(transport.gmail_calls) == 1
    with pytest.raises(EmailDeliveryUncertain, match="unresolved"):
        provider_registry.send("gmail", PAYLOAD, action)
    assert len(transport.gmail_calls) == 1
    ledger_row = provider.ledger.status_for_key("google-sub-123", PAYLOAD["idempotency_key"])
    assert ledger_row["status"] == "uncertain"
    unresolved = provider.ledger.unresolved("google-sub-123")
    assert unresolved[0]["execution_id"] == result.execution_id

    from app.connectors.gmail_reconciliation import GmailExecutionReconciliationService
    reconciliation = GmailExecutionReconciliationService(provider.ledger, journal)
    report = reconciliation.inspect("google-sub-123")
    assert report["counts"]["linked"] == 1
    assert report["attempts"][0]["execution_status"] == "uncertain"
    with pytest.raises(ValueError, match="evidence reference"):
        reconciliation.record_decision(
            "google-sub-123", unresolved[0]["attempt_id"], "confirmed_failed",
            "Provider investigation found no accepted message", ADMIN)
    decision = reconciliation.record_decision(
        "google-sub-123", unresolved[0]["attempt_id"], "confirmed_failed",
        "Provider investigation found no accepted message", ADMIN, "provider-case-123")
    assert decision["execution_id"] == result.execution_id
    assert decision["reconciliation"]["outcome"] == "confirmed_failed"
    assert provider.ledger.status_for_key("google-sub-123", PAYLOAD["idempotency_key"])["status"] == "uncertain"
    assert journal.get(result.execution_id)["status"] == "uncertain"
    assert len(transport.gmail_calls) == 1
    with pytest.raises(EmailDeliveryUncertain, match="unresolved"):
        provider_registry.send("gmail", PAYLOAD, action)
    assert len(transport.gmail_calls) == 1


def test_gmail_sender_rejects_subject_header_injection_before_network(tmp_path):
    _, transport, _, registry = make_connected_sender(tmp_path)
    unsafe = {**PAYLOAD, "subject": "approved\r\nBcc: victim@example.com"}
    with pytest.raises(ValueError, match="line breaks"):
        registry.build_action("gmail", unsafe)
    assert transport.gmail_calls == []


def test_interrupted_gmail_send_ledger_entry_recovers_as_uncertain(tmp_path):
    from app.connectors.gmail_sender import GmailSendLedger
    path = str(tmp_path / "interrupted-ledger.sqlite3")
    first = GmailSendLedger(path)
    first.reserve("account-1", "idempotency-1", "payload-digest")
    restarted = GmailSendLedger(path)
    assert restarted.status_for_key("account-1", "idempotency-1")["status"] == "uncertain"
    with pytest.raises(EmailDeliveryUncertain, match="unresolved"):
        restarted.reserve("account-1", "idempotency-1", "payload-digest")


def test_gmail_ledger_lists_only_safe_unresolved_metadata(tmp_path):
    from app.connectors.gmail_sender import GmailSendLedger
    ledger = GmailSendLedger(str(tmp_path / "investigation-ledger.sqlite3"))
    ledger.reserve("account-safe", "secret-idempotency-key", "private-payload-hash")
    ledger.reserve("another-account", "another-key", "other-payload")
    rows = ledger.unresolved("account-safe")
    assert len(rows) == 1
    assert rows[0]["status"] == "sending"
    assert "secret-idempotency-key" not in repr(rows)
    assert "private-payload-hash" not in repr(rows)
    assert "account-safe" not in repr(rows)
    assert "provider_message_id" in rows[0]


def test_gmail_ledger_unresolved_limit_is_validated(tmp_path):
    from app.connectors.gmail_sender import GmailSendLedger
    ledger = GmailSendLedger(str(tmp_path / "limit-ledger.sqlite3"))
    for invalid in (0, 501, True, "10"):
        with pytest.raises(ValueError, match="limit"):
            ledger.unresolved("account-safe", invalid)
