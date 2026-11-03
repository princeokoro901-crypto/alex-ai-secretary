"""Provider-neutral email integration boundary.

No concrete provider adapter is shipped or registered by default. Adapters are
trusted application code and must own credential retrieval/refresh; credentials
must never be accepted from API payloads or returned in status objects.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from threading import RLock
from typing import Any, Protocol

from app.connectors.sandbox_email import SandboxEmailOutbox
from app.execution import ExecutionOutcomeUncertain

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ALLOWED_SCOPE_CHARS_RE = re.compile(r"^[A-Za-z0-9._:/ -]{1,160}$")


class EmailProviderError(RuntimeError):
    """An email provider is unavailable or rejected a safe delivery request."""


class EmailDeliveryUncertain(EmailProviderError, ExecutionOutcomeUncertain):
    """The provider may have accepted a message but success is not confirmed."""


class EmailProviderAdapter(Protocol):
    """Contract implemented by a trusted, provider-specific adapter."""

    def authorization_status(self) -> dict[str, Any]:
        """Return authorization metadata, never access/refresh tokens."""
        ...

    def send_email(self, message: dict[str, str], idempotency_key: str) -> dict[str, Any]:
        """Send once; adapter must not blindly retry ambiguous external outcomes."""
        ...


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    configured: bool
    authorized: bool
    send_enabled: bool
    account_label: str | None = None
    expires_at: str | None = None
    scopes: tuple[str, ...] = ()
    issue: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "configured": self.configured,
            "authorized": self.authorized,
            "send_enabled": self.send_enabled,
            "account_label": self.account_label,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "issue": self.issue,
        }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_id(value: str) -> str:
    if not isinstance(value, str) or not _PROVIDER_ID_RE.fullmatch(value):
        raise ValueError("provider_id must be 2-64 lowercase letters, digits, '_' or '-' and start with a letter")
    return value


def _authorization_metadata(adapter: EmailProviderAdapter) -> dict[str, Any]:
    try:
        status = adapter.authorization_status()
    except Exception as exc:
        raise EmailProviderError("Provider authorization status is unavailable") from None
    if not isinstance(status, dict) or not isinstance(status.get("authorized"), bool):
        raise EmailProviderError("Provider returned invalid authorization metadata")
    return status


def provider_email_action(provider_id: str, payload: dict, account_id: str) -> str:
    """Create a content- and account-bound approval string without exposing PII."""
    provider_id = _provider_id(provider_id)
    SandboxEmailOutbox.validate(payload)
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("A stable provider account identifier is required")
    key = payload.get("idempotency_key")
    if not key:
        raise ValueError("idempotency_key is required for provider email actions")
    return (
        f"email.send provider={provider_id} account_sha256={_sha256(account_id.strip())} "
        f"to_sha256={_sha256(payload['to'].strip().lower())} "
        f"subject_sha256={_sha256(payload['subject'].strip())} "
        f"body_sha256={_sha256(payload['body'])} idempotency_key_sha256={_sha256(key)}"
    )


class EmailProviderRegistry:
    """Trusted-code registry for provider adapters, fail-closed by default.

    A send is allowed only when the global feature flag is enabled, the provider
    is allowlisted, the adapter reports an authorized account, an idempotency
    key is present, and the caller supplies the exact action string approved
    for the current provider account and message content.
    """

    def __init__(self, *, real_sending_enabled: bool = False,
                 allowed_provider_ids: tuple[str, ...] | list[str] = ()):
        if not isinstance(real_sending_enabled, bool):
            raise ValueError("real_sending_enabled must be a boolean")
        self.real_sending_enabled = real_sending_enabled
        self.allowed_provider_ids = frozenset(_provider_id(item) for item in allowed_provider_ids)
        self._adapters: dict[str, EmailProviderAdapter] = {}
        self._lock = RLock()

    def register(self, provider_id: str, adapter: EmailProviderAdapter) -> None:
        """Register a provider adapter from trusted application code only."""
        provider_id = _provider_id(provider_id)
        if not callable(getattr(adapter, "authorization_status", None)) or not callable(getattr(adapter, "send_email", None)):
            raise ValueError("Provider adapter must implement authorization_status and send_email")
        with self._lock:
            if provider_id in self._adapters:
                raise ValueError("Email provider is already registered")
            self._adapters[provider_id] = adapter

    def _get_adapter(self, provider_id: str) -> EmailProviderAdapter:
        provider_id = _provider_id(provider_id)
        with self._lock:
            adapter = self._adapters.get(provider_id)
        if adapter is None:
            raise EmailProviderError("Email provider is not configured")
        return adapter

    @staticmethod
    def _safe_metadata(provider_id: str, status: dict[str, Any], enabled: bool,
                       allowed: bool) -> ProviderStatus:
        account_id = status.get("account_id")
        authorized = status.get("authorized") is True and isinstance(account_id, str) and bool(account_id.strip())
        raw_label = status.get("account_label")
        account_label = None
        if isinstance(raw_label, str) and raw_label.strip():
            candidate = raw_label.strip()[:120]
            # Do not expose an unmasked email address if an adapter mistakenly
            # returns one as its display label.
            if "@" in candidate and not any(mask in candidate for mask in ("*", "•", "…")):
                account_label = "connected account"
            else:
                account_label = candidate
        raw_expiry = status.get("expires_at")
        expires_at = raw_expiry[:80] if isinstance(raw_expiry, str) and raw_expiry.strip() else None
        raw_scopes = status.get("scopes", [])
        scopes: tuple[str, ...] = ()
        if isinstance(raw_scopes, list):
            clean = [item[:160] for item in raw_scopes if isinstance(item, str) and _ALLOWED_SCOPE_CHARS_RE.fullmatch(item)]
            scopes = tuple(clean[:30])
        return ProviderStatus(
            provider_id=provider_id,
            configured=True,
            authorized=authorized,
            send_enabled=enabled and allowed and authorized,
            account_label=account_label,
            expires_at=expires_at,
            scopes=scopes,
            issue=None if authorized else "authorization_required",
        )

    def status(self) -> dict[str, Any]:
        """Return a redacted inventory; secrets/unknown adapter fields are never exposed."""
        with self._lock:
            provider_ids = sorted(self._adapters)
        providers = []
        for provider_id in provider_ids:
            adapter = self._get_adapter(provider_id)
            try:
                metadata = _authorization_metadata(adapter)
                status = self._safe_metadata(provider_id, metadata, self.real_sending_enabled,
                                             provider_id in self.allowed_provider_ids)
            except EmailProviderError:
                status = ProviderStatus(provider_id, True, False, False, issue="authorization_status_unavailable")
            providers.append(status.public_dict())
        return {
            "real_sending_enabled": self.real_sending_enabled,
            "allowed_provider_ids": sorted(self.allowed_provider_ids),
            "providers": providers,
            "note": "Provider adapters are registered by trusted application code only; no real provider is shipped or registered by default.",
        }

    def build_action(self, provider_id: str, payload: dict) -> str:
        """Build an exact approval action for the currently authorized account."""
        SandboxEmailOutbox.validate(payload)
        if not payload.get("idempotency_key"):
            raise ValueError("idempotency_key is required for provider email actions")
        adapter = self._get_adapter(provider_id)
        status = _authorization_metadata(adapter)
        account_id = status.get("account_id")
        if status.get("authorized") is not True or not isinstance(account_id, str) or not account_id.strip():
            raise EmailProviderError("Provider authorization is required before preparing a real email action")
        return provider_email_action(provider_id, payload, account_id)

    def send(self, provider_id: str, payload: dict, approved_action: str,
             execution_context=None) -> dict[str, Any]:
        """Send once only after rechecking gates and binding; context is gateway-generated."""
        provider_id = _provider_id(provider_id)
        SandboxEmailOutbox.validate(payload)
        key = payload.get("idempotency_key")
        if not key:
            raise EmailProviderError("idempotency_key is required for provider email sends")
        if not self.real_sending_enabled:
            raise EmailProviderError("Real email sending is disabled by configuration")
        if provider_id not in self.allowed_provider_ids:
            raise EmailProviderError("Email provider is not allowlisted")
        adapter = self._get_adapter(provider_id)
        status = _authorization_metadata(adapter)
        account_id = status.get("account_id")
        if status.get("authorized") is not True or not isinstance(account_id, str) or not account_id.strip():
            raise EmailProviderError("Provider authorization is required")
        expected_action = provider_email_action(provider_id, payload, account_id)
        if not isinstance(approved_action, str) or approved_action != expected_action:
            raise EmailProviderError("Approved email action does not match the current provider account and message")
        normalized_message = {
            "to": payload["to"].strip(),
            "subject": payload["subject"].strip(),
            "body": payload["body"],
        }
        try:
            contextual_sender = getattr(adapter, "send_email_with_context", None)
            if execution_context is not None and callable(contextual_sender):
                result = contextual_sender(normalized_message, key, execution_context)
            else:
                result = adapter.send_email(normalized_message, key)
        except EmailDeliveryUncertain:
            raise
        except EmailProviderError:
            # Explicit pre-send validation/provider errors are not ambiguous.
            raise
        except Exception:
            # Do not retry: the provider may have accepted the message before failing.
            raise EmailDeliveryUncertain("Provider send failed or returned an ambiguous outcome; reconcile before retrying") from None
        if not isinstance(result, dict):
            raise EmailDeliveryUncertain("Provider returned an invalid send result; reconcile before retrying")
        provider_message_id = result.get("provider_message_id")
        delivery_status = result.get("status")
        if not isinstance(provider_message_id, str) or not provider_message_id.strip() or delivery_status not in {"accepted", "sent"}:
            raise EmailDeliveryUncertain("Provider returned an incomplete send result; reconcile before retrying")
        return {
            "provider_id": provider_id,
            "provider_message_id": provider_message_id[:200],
            "status": delivery_status,
            "idempotency_key_sha256": _sha256(key),
            "mode": "real_provider",
            "sent": delivery_status == "sent",
            "submitted": True,
            "duplicate_replay": result.get("duplicate_replay") is True,
        }
