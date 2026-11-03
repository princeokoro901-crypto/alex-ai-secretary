"""Explicitly configured OpenAI-compatible chat-completions adapter.

No provider is called unless URL, model, and API key are configured server-side.
The browser never receives credentials. Requests use HTTPS, except optional local
HTTP endpoints when ALEX_LLM_ALLOW_INSECURE_LOCAL=true is explicitly enabled.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler


class LLMNotConfigured(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


def _config(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    base_url = source.get("ALEX_LLM_BASE_URL", "").strip().rstrip("/")
    api_key = source.get("ALEX_LLM_API_KEY", "").strip()
    model = source.get("ALEX_LLM_MODEL", "").strip()
    if not (base_url and api_key and model):
        raise LLMNotConfigured("AI provider is not configured. Set the server-side URL, API key, and model.")
    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
    except ValueError:
        raise LLMNotConfigured("AI provider URL is invalid.") from None
    local_http_allowed = source.get("ALEX_LLM_ALLOW_INSECURE_LOCAL", "false").strip().lower() == "true"
    is_loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback and local_http_allowed):
        raise LLMNotConfigured("AI provider URL must use HTTPS; local HTTP requires explicit local-only opt-in.")
    if not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMNotConfigured("AI provider URL is invalid.")
    return {"base_url": base_url, "api_key": api_key, "model": model}


def provider_status(*, env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return safe provider readiness metadata without exposing credentials or URL.

    This is a configuration check only; it does not contact the provider.
    """
    source = os.environ if env is None else env
    required = ("ALEX_LLM_BASE_URL", "ALEX_LLM_API_KEY", "ALEX_LLM_MODEL")
    if any(not str(source.get(name, "")).strip() for name in required):
        return {
            "state": "not_configured",
            "configured": False,
            "connection_tested": False,
            "model_label": None,
            "message": "AI provider is not fully configured on the server.",
        }
    try:
        config = _config(source)
    except LLMNotConfigured:
        return {
            "state": "invalid_configuration",
            "configured": False,
            "connection_tested": False,
            "model_label": None,
            "message": "AI provider configuration needs correction before use.",
        }
    return {
        "state": "configured_not_tested",
        "configured": True,
        "connection_tested": False,
        "model_label": config["model"][:100],
        "message": "Provider settings are present and pass local checks; no live connection test has been performed.",
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential-bearing request to a redirected URL.
        return None


def _http_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        opener = build_opener(_NoRedirect)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise LLMProviderError("AI provider response exceeded the allowed size.")
            return raw
    except HTTPError as exc:
        # Do not surface provider response bodies; they can contain sensitive details.
        raise LLMProviderError(f"AI provider returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise LLMProviderError("AI provider could not be reached or timed out.") from None


def chat_completion(messages: list[dict[str, str]], *, env: Mapping[str, str] | None = None,
                    transport: Callable[[str, dict[str, str], bytes, float], bytes] | None = None) -> tuple[str, str]:
    config = _config(env)
    endpoint = config["base_url"] + "/chat/completions"
    payload = json.dumps({"model": config["model"], "messages": messages,
                          "temperature": 0.2, "max_tokens": 1800}).encode("utf-8")
    sender = transport or _http_transport
    try:
        raw = sender(endpoint, {"Authorization": f"Bearer {config['api_key']}",
                                "Content-Type": "application/json",
                                "Accept": "application/json"}, payload, 25.0)
        decoded = json.loads(raw.decode("utf-8"))
        content = decoded["choices"][0]["message"]["content"]
        # Trust the configured model identifier, not a provider-controlled label.
    except LLMProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
        raise LLMProviderError("AI provider returned an invalid response format.") from None
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError("AI provider returned an empty response.")
    if len(content) > 20000:
        raise LLMProviderError("AI provider response exceeded the allowed output size.")
    # Return only a non-secret model label; never return or log credentials.
    model_label = config["model"][:100]
    return content.strip(), model_label


def test_provider_connection(*, env: Mapping[str, str] | None = None,
                            transport: Callable[[str, dict[str, str], bytes, float], bytes] | None = None) -> dict[str, object]:
    """Perform a small, explicit live connectivity test without exposing model output.

    This sends a short probe to the configured provider. It does not send user notes,
    draft a report, or perform external actions. The returned result is safe metadata.
    """
    config = _config(env)
    endpoint = config["base_url"] + "/chat/completions"
    payload = json.dumps({
        "model": config["model"],
        "messages": [
            {"role": "system", "content": "You are performing a connectivity check. Reply with a short acknowledgement only."},
            {"role": "user", "content": "Reply with: ALEX_CONNECTION_OK"},
        ],
        "temperature": 0,
        "max_tokens": 12,
    }).encode("utf-8")
    sender = transport or _http_transport
    try:
        raw = sender(endpoint, {"Authorization": f"Bearer {config['api_key']}",
                                "Content-Type": "application/json",
                                "Accept": "application/json"}, payload, 8.0)
        decoded = json.loads(raw.decode("utf-8"))
        content = decoded["choices"][0]["message"]["content"]
    except LLMProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
        raise LLMProviderError("AI provider returned an invalid response format.") from None
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError("AI provider returned an empty response.")
    return {"state": "connected", "connection_tested": True,
            "model_label": config["model"][:100],
            "message": "The configured AI provider returned a valid response to the connection test."}
