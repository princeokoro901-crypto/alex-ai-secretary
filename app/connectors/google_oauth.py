"""Disabled-by-default Google OAuth adapter for Alex email integrations.

This module builds Google authorization URLs and completes the server-side
OAuth code exchange through an injectable transport. It intentionally does not
register an email sender or expose HTTP callback routes; browser-session/CSRF
integration and provider-specific security review are required before that step.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPRedirectHandler, Request, build_opener)

from app.connectors.oauth_security import (
    OAuthAuthorizationError,
    OAuthAuthorizationService,
    OAuthCredentialError,
    OAuthCredentialVault,
)


GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_REQUIRED_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.send",
)


class GoogleOAuthError(RuntimeError):
    """Google OAuth flow failed without exposing provider secrets or responses."""


class GoogleGmailOutcomeUncertain(GoogleOAuthError):
    """A Gmail request may have reached Google, but success was not confirmed."""


class GoogleOAuthTransport(Protocol):
    def post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]: ...
    def get_bearer_json(self, url: str, access_token: str) -> dict[str, Any]: ...
    def revoke_token(self, token: str) -> None: ...
    def send_gmail_message(self, access_token: str, raw_message: str) -> dict[str, Any]: ...


class _NoRedirect(HTTPRedirectHandler):
    """Do not forward OAuth codes/client credentials to redirected endpoints."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibGoogleOAuthTransport:
    """Small HTTPS-only transport with redirects disabled and bounded responses."""

    MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, timeout_seconds: float = 10.0):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        self.timeout_seconds = float(timeout_seconds)
        self._opener = build_opener(_NoRedirect())

    def _request_json(self, request: Request) -> dict[str, Any]:
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise GoogleOAuthError("Google OAuth provider response exceeded the size limit")
            decoded = json.loads(body.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise GoogleOAuthError("Google OAuth provider returned an invalid response")
            return decoded
        except GoogleOAuthError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Deliberately omit provider response bodies, request URLs with query
            # values, and exception strings because they can contain secrets.
            raise GoogleOAuthError("Google OAuth provider request failed") from None

    def post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        if url != GOOGLE_TOKEN_ENDPOINT:
            raise GoogleOAuthError("Untrusted Google OAuth token endpoint")
        request = Request(
            url,
            data=urlencode(form).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        return self._request_json(request)

    def get_bearer_json(self, url: str, access_token: str) -> dict[str, Any]:
        if url != GOOGLE_USERINFO_ENDPOINT:
            raise GoogleOAuthError("Untrusted Google OAuth identity endpoint")
        request = Request(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            method="GET",
        )
        return self._request_json(request)

    def send_gmail_message(self, access_token: str, raw_message: str) -> dict[str, Any]:
        """Submit one RFC 2822/base64url message to the fixed Gmail API endpoint."""
        if not isinstance(access_token, str) or not access_token.strip() or len(access_token) > 16384:
            raise GoogleOAuthError("Google access token is invalid")
        if not isinstance(raw_message, str) or not raw_message.strip() or len(raw_message) > 2_000_000:
            raise GoogleOAuthError("Encoded Gmail message is invalid")
        request = Request(
            GOOGLE_GMAIL_SEND_ENDPOINT,
            data=json.dumps({"raw": raw_message}, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        return self._request_json(request)

    def revoke_token(self, token: str) -> None:
        """Revoke a token at Google's fixed HTTPS endpoint without logging it."""
        if not isinstance(token, str) or not token.strip() or len(token) > 16384:
            raise GoogleOAuthError("Google OAuth revocation token is invalid")
        request = Request(
            GOOGLE_REVOCATION_ENDPOINT,
            data=urlencode({"token": token}).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(body) > self.MAX_RESPONSE_BYTES:
                    raise GoogleOAuthError("Google OAuth revocation response exceeded the size limit")
                if not 200 <= response.status < 300:
                    raise GoogleOAuthError("Google OAuth provider revocation failed")
        except GoogleOAuthError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError):
            # Never include provider response bodies or the submitted token.
            raise GoogleOAuthError("Google OAuth provider revocation failed") from None


class GoogleOAuthAdapter:
    """Server-side Google OAuth flow backed by Alex's state service and token vault.

    The adapter is disabled unless ``enabled=True`` is supplied by trusted
    application configuration. The authorization-code callback must run only
    on a server-side route that has established the same actor/key context as
    the initiating request. No API route is registered by this module.
    """

    provider_id = "gmail"
    required_scopes = frozenset(GOOGLE_REQUIRED_SCOPES)
    # Google may report OIDC short names or their documented userinfo URI aliases.
    required_scope_aliases = (
        frozenset({"openid"}),
        frozenset({"email", "https://www.googleapis.com/auth/userinfo.email"}),
        frozenset({"profile", "https://www.googleapis.com/auth/userinfo.profile"}),
        frozenset({"https://www.googleapis.com/auth/gmail.send"}),
    )

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorization_service: OAuthAuthorizationService,
        credential_vault: OAuthCredentialVault,
        enabled: bool = False,
        transport: GoogleOAuthTransport | None = None,
        scopes: tuple[str, ...] | list[str] = GOOGLE_REQUIRED_SCOPES,
        clock=time.time,
    ):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 512:
            raise ValueError("client_id must be configured")
        if not isinstance(client_secret, str) or not client_secret.strip() or len(client_secret) > 4096:
            raise ValueError("client_secret must be configured")
        if isinstance(scopes, (str, bytes)) or not isinstance(scopes, (tuple, list)):
            raise ValueError("scopes must be a list of individual scope strings")
        clean_scopes = tuple(dict.fromkeys(item.strip() for item in scopes if isinstance(item, str) and item.strip()))
        if len(clean_scopes) != len(scopes) or not self.required_scopes.issubset(clean_scopes):
            raise ValueError("Google email integration must request all required identity and Gmail send scopes")
        if authorization_service.redirect_uris.get(self.provider_id) is None:
            raise ValueError("OAuthAuthorizationService must configure the gmail redirect URI")
        self.client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self.authorization_service = authorization_service
        self.credential_vault = credential_vault
        self.enabled = enabled
        self.transport = transport or UrllibGoogleOAuthTransport()
        self.scopes = clean_scopes
        self._clock = clock

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise GoogleOAuthError("Google OAuth integration is disabled by configuration")

    def begin(self, actor_id: str, key_id: str,
              browser_session_binding: str | None = None) -> dict[str, Any]:
        """Create an authorization URL; bind it to a browser when supplied."""
        self._require_enabled()
        transaction = self.authorization_service.start(
            self.provider_id, actor_id, key_id, browser_session_binding=browser_session_binding
        )
        params = {
            "client_id": self.client_id,
            "redirect_uri": transaction["redirect_uri"],
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": transaction["state"],
            "code_challenge": transaction["code_challenge"],
            "code_challenge_method": "S256",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        return {
            "provider_id": self.provider_id,
            "authorization_url": f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}",
            "state": transaction["state"],
            "expires_at": transaction["expires_at"],
            "code_challenge_method": "S256",
            "note": "Authorization URL only; credentials are exchanged only after a trusted server-side callback validates the browser binding.",
        }

    @staticmethod
    def _nonempty_secret(value: Any, label: str, maximum: int = 16384) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise GoogleOAuthError(f"Google OAuth response did not contain a valid {label}")
        return value.strip()

    @staticmethod
    def _mask_email(email: str) -> str:
        local, domain = email.split("@", 1)
        visible = local[:1] if local else "*"
        return f"{visible}{'***' if len(local) > 1 else '**'}@{domain[:1]}***"

    @staticmethod
    def _validate_code(code: str) -> str:
        if not isinstance(code, str) or not code.strip() or len(code) > 4096:
            raise GoogleOAuthError("Google OAuth authorization code is invalid")
        return code.strip()

    def complete(self, *, code: str, state: str, actor_id: str, key_id: str) -> dict[str, Any]:
        """Complete a trusted server-side callback when actor/key context is known."""
        self._require_enabled()
        clean_code = self._validate_code(code)
        try:
            context = self.authorization_service.consume_callback(
                self.provider_id,
                state,
                actor_id,
                key_id,
                self.authorization_service.redirect_uris[self.provider_id],
            )
        except (OAuthAuthorizationError, ValueError):
            raise GoogleOAuthError("Google OAuth state is invalid, expired, or does not match this actor") from None
        return self._exchange_and_store(clean_code, context)

    def complete_browser(self, *, code: str, state: str,
                         browser_session_binding: str) -> dict[str, Any]:
        """Complete callback using a browser binding and server-stored actor identity."""
        self._require_enabled()
        clean_code = self._validate_code(code)
        try:
            context = self.authorization_service.consume_callback_browser(
                self.provider_id,
                state,
                browser_session_binding,
                self.authorization_service.redirect_uris[self.provider_id],
            )
        except (OAuthAuthorizationError, ValueError):
            raise GoogleOAuthError("Google OAuth state is invalid, expired, or does not match this browser") from None
        result = self._exchange_and_store(clean_code, context)
        # Internal-only audit metadata; the HTTP callback never serializes this object.
        result["_audit_actor_id"] = context["actor_id"]
        result["_audit_key_id"] = context["key_id"]
        return result

    def cancel_browser(self, *, state: str, browser_session_binding: str) -> dict[str, str]:
        """Consume a denied/error callback so the state cannot later be replayed."""
        self._require_enabled()
        try:
            return self.authorization_service.consume_callback_browser(
                self.provider_id,
                state,
                browser_session_binding,
                self.authorization_service.redirect_uris[self.provider_id],
            )
        except (OAuthAuthorizationError, ValueError):
            raise GoogleOAuthError("Google OAuth state is invalid, expired, or does not match this browser") from None

    def _exchange_and_store(self, code: str, context: dict[str, str]) -> dict[str, Any]:
        token_form = {
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            "code": code,
            "code_verifier": context["code_verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": context["redirect_uri"],
        }
        try:
            token_response = self.transport.post_form(GOOGLE_TOKEN_ENDPOINT, token_form)
            if not isinstance(token_response, dict):
                raise GoogleOAuthError("Google OAuth token response is invalid")
            access_token = self._nonempty_secret(token_response.get("access_token"), "access token")
            token_type = token_response.get("token_type")
            if not isinstance(token_type, str) or token_type.lower() != "bearer":
                raise GoogleOAuthError("Google OAuth token type is unsupported")
            expires_in = token_response.get("expires_in")
            if isinstance(expires_in, bool) or not isinstance(expires_in, int) or not 1 <= expires_in <= 31_536_000:
                raise GoogleOAuthError("Google OAuth token expiry is invalid")
            raw_scopes = token_response.get("scope")
            if raw_scopes is None:
                granted_scopes = set(self.scopes)
            elif isinstance(raw_scopes, str):
                granted_scopes = set(raw_scopes.split())
            else:
                raise GoogleOAuthError("Google OAuth granted scopes are invalid")
            if not all(aliases.intersection(granted_scopes) for aliases in self.required_scope_aliases):
                raise GoogleOAuthError("Google OAuth did not grant all required scopes")
            refresh_token = token_response.get("refresh_token")
            if refresh_token is not None:
                refresh_token = self._nonempty_secret(refresh_token, "refresh token")
            identity = self.transport.get_bearer_json(GOOGLE_USERINFO_ENDPOINT, access_token)
            if not isinstance(identity, dict):
                raise GoogleOAuthError("Google OAuth identity response is invalid")
            account_id = self._nonempty_secret(identity.get("sub"), "stable account identity", 512)
            email = self._nonempty_secret(identity.get("email"), "account email", 512)
            if (email.count("@") != 1 or any(char.isspace() for char in email)
                    or not all(email.split("@")) or "." not in email.rsplit("@", 1)[1]
                    or identity.get("email_verified") is not True):
                raise GoogleOAuthError("Google account email could not be verified")
            expires_at = self._clock() + expires_in
            self.credential_vault.store_tokens(
                self.provider_id,
                account_id,
                access_token,
                refresh_token,
                expires_at,
                sorted(granted_scopes),
                identity_verified=True,
                owner_actor_id=context["actor_id"],
                account_label=self._mask_email(email),
            )
        except GoogleOAuthError:
            raise
        except (OAuthCredentialError, ValueError):
            raise GoogleOAuthError("Google OAuth credentials could not be safely stored") from None
        except Exception:
            # Never surface provider payloads, transport errors, or token values.
            raise GoogleOAuthError("Google OAuth exchange or identity verification failed; restart authorization") from None

        return {
            "provider_id": self.provider_id,
            "authorized": True,
            "account_label": self._mask_email(email),
            "expires_at": expires_at,
            "scopes": sorted(granted_scopes),
            "note": "Google identity was verified and credentials were stored encrypted; no email was sent.",
        }

    def send_gmail_message(self, account_id: str, actor_id: str, raw_message: str) -> dict[str, Any]:
        """Send one message for the owning actor; ambiguous network outcomes are never retried."""
        self._require_enabled()
        if not isinstance(raw_message, str) or not raw_message.strip() or len(raw_message) > 2_000_000:
            raise GoogleOAuthError("Encoded Gmail message is invalid")
        status = self.local_status(account_id, actor_id)
        if not status.get("configured") or not status.get("authorized"):
            raise GoogleOAuthError("Google account is not authorized; reconnect or inspect account status")
        scopes = status.get("scopes", [])
        if not isinstance(scopes, list) or "https://www.googleapis.com/auth/gmail.send" not in scopes:
            raise GoogleOAuthError("Google account is missing the Gmail send permission")
        expiry = status.get("expires_at")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
            raise GoogleOAuthError("Google access-token expiry is unavailable")
        if expiry <= self._clock() + 60:
            self.refresh_account_tokens(account_id, actor_id)
        try:
            tokens = self.credential_vault.get_tokens_for_adapter(self.provider_id, account_id, actor_id)
        except (OAuthCredentialError, ValueError):
            raise GoogleOAuthError("Google account credentials are unavailable") from None
        # From this point a transport failure may occur after Google accepted the
        # message. Treat all transport errors and malformed receipts as uncertain.
        try:
            sender = getattr(self.transport, "send_gmail_message", None)
            if not callable(sender):
                raise GoogleGmailOutcomeUncertain("Gmail send transport is unavailable; reconcile before retrying")
            receipt = sender(str(tokens["access_token"]), raw_message)
        except GoogleGmailOutcomeUncertain:
            raise
        except Exception:
            raise GoogleGmailOutcomeUncertain("Gmail request failed or its outcome is ambiguous; reconcile before retrying") from None
        if not isinstance(receipt, dict):
            raise GoogleGmailOutcomeUncertain("Gmail returned no usable receipt; reconcile before retrying")
        message_id = receipt.get("id")
        if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 512:
            raise GoogleGmailOutcomeUncertain("Gmail returned an incomplete receipt; reconcile before retrying")
        return {"provider_message_id": message_id, "status": "sent"}

    def local_status(self, account_id: str, actor_id: str) -> dict[str, Any]:
        """Return redacted status only for the actor who owns the linked account."""
        status = self.credential_vault.status(self.provider_id, account_id, actor_id)
        return {
            "provider_id": status["provider_id"],
            "account_id": status.get("account_id", account_id),
            "account_label": status.get("account_label"),
            "configured": status["configured"],
            "authorized": status["authorized"],
            "access_token_expired": status.get("access_token_expired", False),
            "refresh_available": status.get("refresh_available", False),
            "refresh_in_progress": status.get("refresh_in_progress", False),
            "expires_at": status.get("expires_at"),
            "scopes": status.get("scopes", []),
            "issue": status.get("issue"),
        }

    def list_accounts(self, actor_id: str) -> list[dict[str, Any]]:
        """Return redacted connected-account metadata owned by the caller."""
        self._require_enabled()
        return self.credential_vault.list_accounts_for_owner(self.provider_id, actor_id)

    def refresh_account_tokens(self, account_id: str, actor_id: str) -> dict[str, Any]:
        """Refresh an expiring token under an atomic lease; never return token values."""
        self._require_enabled()
        try:
            claim = self.credential_vault.claim_token_refresh(
                self.provider_id, account_id, actor_id, safety_window_seconds=60, lease_seconds=30,
            )
        except (OAuthCredentialError, ValueError):
            raise GoogleOAuthError("Google account cannot be refreshed; inspect status or reconnect it") from None
        if claim["status"] == "not_needed":
            result = self.local_status(account_id, actor_id)
            result["refresh_performed"] = False
            return result

        lease_token = str(claim["lease_token"])
        try:
            token_response = self.transport.post_form(GOOGLE_TOKEN_ENDPOINT, {
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": str(claim["refresh_token"]),
            })
            if not isinstance(token_response, dict):
                raise GoogleOAuthError("Google OAuth refresh response is invalid")
            access_token = self._nonempty_secret(token_response.get("access_token"), "access token")
            token_type = token_response.get("token_type")
            if not isinstance(token_type, str) or token_type.lower() != "bearer":
                raise GoogleOAuthError("Google OAuth refresh token type is unsupported")
            expires_in = token_response.get("expires_in")
            if isinstance(expires_in, bool) or not isinstance(expires_in, int) or not 1 <= expires_in <= 31_536_000:
                raise GoogleOAuthError("Google OAuth refresh expiry is invalid")
            raw_scopes = token_response.get("scope")
            if raw_scopes is None:
                granted_scopes = list(claim["scopes"])
            elif isinstance(raw_scopes, str):
                granted_scopes = list(dict.fromkeys(raw_scopes.split()))
            else:
                raise GoogleOAuthError("Google OAuth refresh scopes are invalid")
            if not all(aliases.intersection(granted_scopes) for aliases in self.required_scope_aliases):
                raise GoogleOAuthError("Google OAuth refresh did not preserve required permissions")
            rotated_refresh = token_response.get("refresh_token")
            if rotated_refresh is not None:
                rotated_refresh = self._nonempty_secret(rotated_refresh, "refresh token")
            saved = self.credential_vault.complete_token_refresh(
                self.provider_id, account_id, actor_id, lease_token, access_token,
                rotated_refresh, self._clock() + expires_in, granted_scopes,
            )
            if not saved:
                raise GoogleOAuthError("Google OAuth refresh lease expired or account was disconnected")
        except GoogleOAuthError:
            self.credential_vault.release_token_refresh(self.provider_id, account_id, actor_id, lease_token)
            raise
        except Exception:
            self.credential_vault.release_token_refresh(self.provider_id, account_id, actor_id, lease_token)
            raise GoogleOAuthError("Google OAuth refresh failed; inspect status or reconnect the account") from None

        result = self.local_status(account_id, actor_id)
        result["refresh_performed"] = True
        result["note"] = "Access token refreshed and stored encrypted; token values were not returned."
        return result

    def disconnect_account(self, account_id: str, actor_id: str) -> dict[str, Any]:
        """Revoke at Google when possible, then always disable local credentials."""
        self._require_enabled()
        try:
            tokens = self.credential_vault.get_tokens_for_adapter(self.provider_id, account_id, actor_id)
        except (OAuthCredentialError, ValueError):
            raise GoogleOAuthError("Google account is unavailable for this actor") from None

        provider_revocation = "failed"
        revoke_token = tokens.get("refresh_token") or tokens.get("access_token")
        try:
            revoke = getattr(self.transport, "revoke_token", None)
            if not callable(revoke):
                raise GoogleOAuthError("Provider revocation transport is unavailable")
            revoke(str(revoke_token))
            provider_revocation = "succeeded"
        except Exception:
            # A provider outage must not keep Alex locally authorized.
            provider_revocation = "failed"
        local_revoked = self.credential_vault.revoke(self.provider_id, account_id, actor_id)
        status = self.credential_vault.status(self.provider_id, account_id, actor_id)
        if not local_revoked and status.get("issue") != "revoked":
            raise GoogleOAuthError("Google account could not be safely disconnected locally")
        return {
            "provider_id": self.provider_id,
            "account_id": account_id,
            "local_disconnected": status.get("issue") == "revoked",
            "provider_revocation": provider_revocation,
            "note": ("Local credentials are disabled. Confirm provider-side revocation in Google account settings."
                     if provider_revocation != "succeeded" else "Local credentials are disabled and Google accepted the revocation request."),
        }
