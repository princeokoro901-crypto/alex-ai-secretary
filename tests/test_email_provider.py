import pytest

from app.connectors.email_provider import EmailProviderError, EmailProviderRegistry


class FakeEmailProvider:
    def __init__(self, *, authorized=True, account_id="account-123", result=None, error=None):
        self.authorized = authorized
        self.account_id = account_id
        self.result = result or {"provider_message_id": "provider-msg-9", "status": "accepted"}
        self.error = error
        self.calls = []

    def authorization_status(self):
        return {
            "authorized": self.authorized,
            "account_id": self.account_id,
            "account_label": "p***@example.com",
            "expires_at": "2027-01-01T00:00:00Z",
            "scopes": ["email.send", "email.read"],
            # The registry must never surface arbitrary fields from an adapter.
            "access_token": "DO_NOT_EXPOSE_THIS_TOKEN",
            "refresh_token": "DO_NOT_EXPOSE_THIS_REFRESH_TOKEN",
        }

    def send_email(self, message, idempotency_key):
        self.calls.append((message, idempotency_key))
        if self.error:
            raise self.error
        return self.result


PAYLOAD = {
    "to": "person@example.com",
    "subject": "Approved report",
    "body": "Please find the report attached.",
    "idempotency_key": "mission-44-email",
}


def configured_registry(provider, *, enabled=True, allowed=("gmail",)):
    registry = EmailProviderRegistry(real_sending_enabled=enabled, allowed_provider_ids=allowed)
    registry.register("gmail", provider)
    return registry


def test_default_registry_is_fail_closed_and_empty():
    status = EmailProviderRegistry().status()
    assert status["real_sending_enabled"] is False
    assert status["providers"] == []


def test_provider_status_redacts_tokens_and_unknown_fields():
    provider = FakeEmailProvider()
    status = configured_registry(provider).status()
    assert status["providers"][0]["authorized"] is True
    assert status["providers"][0]["send_enabled"] is True
    assert status["providers"][0]["account_label"] == "p***@example.com"
    serialized = repr(status)
    assert "DO_NOT_EXPOSE_THIS_TOKEN" not in serialized
    assert "DO_NOT_EXPOSE_THIS_REFRESH_TOKEN" not in serialized
    assert "account-123" not in serialized


def test_real_send_requires_feature_flag_before_provider_call():
    provider = FakeEmailProvider()
    registry = configured_registry(provider, enabled=False)
    action = registry.build_action("gmail", PAYLOAD)
    with pytest.raises(EmailProviderError, match="disabled"):
        registry.send("gmail", PAYLOAD, action)
    assert provider.calls == []


def test_real_send_requires_provider_allowlist():
    provider = FakeEmailProvider()
    registry = configured_registry(provider, enabled=True, allowed=())
    action = registry.build_action("gmail", PAYLOAD)
    with pytest.raises(EmailProviderError, match="not allowlisted"):
        registry.send("gmail", PAYLOAD, action)
    assert provider.calls == []


def test_unauthorized_provider_cannot_build_or_send_action():
    provider = FakeEmailProvider(authorized=False)
    registry = configured_registry(provider)
    with pytest.raises(EmailProviderError, match="authorization"):
        registry.build_action("gmail", PAYLOAD)
    assert provider.calls == []
    status = registry.status()["providers"][0]
    assert status["send_enabled"] is False
    assert status["issue"] == "authorization_required"


def test_approved_action_binds_provider_account_and_exact_message():
    provider = FakeEmailProvider()
    registry = configured_registry(provider)
    action = registry.build_action("gmail", PAYLOAD)
    assert "person@example.com" not in action
    assert "Approved report" not in action
    assert registry.build_action("gmail", {**PAYLOAD, "body": "Changed body"}) != action
    provider.account_id = "different-account"
    with pytest.raises(EmailProviderError, match="does not match"):
        registry.send("gmail", PAYLOAD, action)
    assert provider.calls == []


def test_send_returns_provider_receipt_without_credentials():
    provider = FakeEmailProvider()
    registry = configured_registry(provider)
    action = registry.build_action("gmail", PAYLOAD)
    result = registry.send("gmail", PAYLOAD, action)
    assert result["provider_id"] == "gmail"
    assert result["provider_message_id"] == "provider-msg-9"
    assert result["status"] == "accepted"
    assert result["submitted"] is True
    assert result["sent"] is False  # provider acceptance is not proof of delivery
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == PAYLOAD["idempotency_key"]
    assert "access_token" not in result


def test_ambiguous_provider_failure_is_not_retried():
    provider = FakeEmailProvider(error=TimeoutError("simulated timeout"))
    registry = configured_registry(provider)
    action = registry.build_action("gmail", PAYLOAD)
    with pytest.raises(EmailProviderError, match="reconcile before retrying"):
        registry.send("gmail", PAYLOAD, action)
    assert len(provider.calls) == 1


def test_incomplete_provider_receipt_is_treated_as_ambiguous():
    provider = FakeEmailProvider(result={"status": "accepted"})
    registry = configured_registry(provider)
    action = registry.build_action("gmail", PAYLOAD)
    with pytest.raises(EmailProviderError, match="incomplete send result"):
        registry.send("gmail", PAYLOAD, action)
    assert len(provider.calls) == 1


def test_idempotency_key_is_required_for_provider_action():
    provider = FakeEmailProvider()
    registry = configured_registry(provider)
    with pytest.raises(ValueError, match="idempotency_key"):
        registry.build_action("gmail", {key: value for key, value in PAYLOAD.items() if key != "idempotency_key"})
    assert provider.calls == []


def test_duplicate_provider_registration_and_untrusted_adapter_rejected():
    registry = EmailProviderRegistry()
    provider = FakeEmailProvider()
    registry.register("gmail", provider)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("gmail", provider)
    with pytest.raises(ValueError, match="implement"):
        registry.register("outlook", object())


def test_authorization_metadata_failure_fails_closed():
    class BrokenProvider(FakeEmailProvider):
        def authorization_status(self):
            raise RuntimeError("token details should not escape")

    registry = configured_registry(BrokenProvider())
    status = registry.status()["providers"][0]
    assert status["authorized"] is False
    assert status["send_enabled"] is False
    assert status["issue"] == "authorization_status_unavailable"
    assert "token details" not in repr(status)


def test_unmasked_email_account_label_is_not_returned():
    provider = FakeEmailProvider()
    provider.authorization_status = lambda: {
        "authorized": True, "account_id": "account-123",
        "account_label": "prince@example.com", "access_token": "secret",
    }
    status = configured_registry(provider).status()["providers"][0]
    assert status["account_label"] == "connected account"
    assert "prince@example.com" not in repr(status)
    assert "secret" not in repr(status)
