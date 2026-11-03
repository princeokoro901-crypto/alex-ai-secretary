"""Read-only deployment readiness diagnostics for Alex Core.

The report exposes configuration presence and safe status metadata only. It never
returns configured paths, API keys, OAuth tokens, account IDs, or message data.
Readiness is advisory and does not enable integrations or mutate operational state.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _storage_status(path: Any) -> tuple[bool, bool]:
    """Return (configured, accessible) without creating or modifying a file."""
    if not isinstance(path, (str, os.PathLike)) or not str(path).strip():
        return False, False
    raw = Path(path).expanduser()
    try:
        if raw.exists():
            return True, raw.is_file() and os.access(raw, os.R_OK | os.W_OK)
        parent = raw.parent if str(raw.parent) else Path(".")
        return True, parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    except (OSError, ValueError):
        return True, False


def _check(check_id: str, passed: bool, detail: str, *, required: bool = True,
           configured: bool = True) -> dict[str, Any]:
    if passed:
        status = "pass"
    elif not configured and not required:
        status = "warning"
    else:
        status = "blocked" if required else "warning"
    return {"id": check_id, "status": status, "required_for_live_gmail": required,
            "detail": detail}


def build_readiness_report(*, api_keys: list[dict] | None, api_key_config_error: bool,
                           orchestration_state: Any, audit_log: Any,
                           approval_service: Any, execution_journal: Any,
                           google_oauth_adapter: Any, gmail_email_provider: Any,
                           real_email_enabled: bool,
                           allowed_email_providers: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Build a non-mutating readiness report from already-initialized services."""
    checks: list[dict[str, Any]] = []

    active_admin = bool(api_keys) and any(
        item.get("active", True) and item.get("role") == "admin" for item in api_keys
    )
    checks.append(_check(
        "api_admin_authentication", active_admin and not api_key_config_error,
        "An active administrator API key is configured." if active_admin and not api_key_config_error
        else "Configure a valid API-key set containing an active administrator key.",
    ))

    orchestration_configured, orchestration_accessible = _storage_status(
        getattr(orchestration_state, "path", None)
    )
    checks.append(_check(
        "orchestration_storage", orchestration_configured and orchestration_accessible,
        "Orchestration state storage is configured and accessible." if orchestration_configured and orchestration_accessible
        else "Configure accessible persistent orchestration storage.",
    ))

    audit_configured, audit_accessible = _storage_status(getattr(audit_log, "path", None))
    checks.append(_check(
        "audit_storage", audit_configured and audit_accessible,
        "Audit storage is configured and accessible." if audit_configured and audit_accessible
        else "Configure accessible persistent audit storage.",
    ))
    try:
        audit_integrity = bool(audit_log.verify())
    except Exception:
        audit_integrity = False
    checks.append(_check(
        "audit_integrity", audit_integrity,
        "The current audit chain verifies." if audit_integrity else "Audit verification failed; investigate before enabling live delivery.",
    ))

    approval_configured, approval_accessible = _storage_status(getattr(approval_service, "path", None))
    checks.append(_check(
        "approval_storage", approval_configured and approval_accessible,
        "Approval storage is configured and accessible." if approval_configured and approval_accessible
        else "Configure accessible persistent approval storage.",
    ))

    journal_configured, journal_accessible = _storage_status(getattr(execution_journal, "path", None))
    checks.append(_check(
        "execution_journal_storage", journal_configured and journal_accessible,
        "Execution journal storage is configured and accessible." if journal_configured and journal_accessible
        else "Configure accessible persistent execution-journal storage.",
    ))

    oauth_enabled = bool(getattr(google_oauth_adapter, "enabled", False))
    checks.append(_check(
        "google_oauth_enabled", oauth_enabled,
        "Google OAuth is enabled." if oauth_enabled else "Google OAuth is not enabled.",
        required=True, configured=oauth_enabled,
    ))

    state_service = getattr(google_oauth_adapter, "authorization_service", None)
    vault = getattr(google_oauth_adapter, "credential_vault", None)
    oauth_state_configured, oauth_state_accessible = _storage_status(getattr(state_service, "path", None))
    checks.append(_check(
        "oauth_state_storage", oauth_state_configured and oauth_state_accessible,
        "OAuth state storage is configured and accessible." if oauth_state_configured and oauth_state_accessible
        else "Configure accessible persistent OAuth state storage.",
    ))
    vault_configured, vault_accessible = _storage_status(getattr(vault, "path", None))
    checks.append(_check(
        "oauth_credential_storage", vault_configured and vault_accessible,
        "Encrypted OAuth credential storage is configured and accessible." if vault_configured and vault_accessible
        else "Configure accessible encrypted OAuth credential storage.",
    ))

    sender_enabled = gmail_email_provider is not None
    checks.append(_check(
        "gmail_sender_enabled", sender_enabled,
        "The Gmail sender is registered." if sender_enabled else "The Gmail sender is disabled; no live delivery is available.",
    ))
    ledger_configured, ledger_accessible = _storage_status(
        getattr(getattr(gmail_email_provider, "ledger", None), "path", None)
    )
    checks.append(_check(
        "gmail_send_ledger_storage", sender_enabled and ledger_configured and ledger_accessible,
        "Gmail send-ledger storage is configured and accessible." if sender_enabled and ledger_configured and ledger_accessible
        else "Configure accessible persistent Gmail send-ledger storage.",
    ))

    global_enabled = real_email_enabled is True
    checks.append(_check(
        "real_email_feature_flag", global_enabled,
        "The global real-email feature flag is enabled." if global_enabled else "Real email delivery is disabled by configuration.",
    ))
    allowlisted = "gmail" in set(allowed_email_providers or [])
    checks.append(_check(
        "gmail_provider_allowlist", allowlisted,
        "Gmail is present in the configured provider allowlist." if allowlisted else "Gmail is not enabled in the provider allowlist.",
    ))

    account_authorized = False
    if sender_enabled:
        try:
            account_authorized = gmail_email_provider.authorization_status().get("authorized") is True
        except Exception:
            account_authorized = False
    checks.append(_check(
        "gmail_account_authorization", account_authorized,
        "The configured account reports valid Gmail-send authorization." if account_authorized
        else "The configured Google account is not confirmed as authorized for Gmail sending.",
    ))

    blockers = [item["id"] for item in checks if item["required_for_live_gmail"] and item["status"] != "pass"]
    warnings = [item["id"] for item in checks if item["status"] == "warning"]
    return {
        "schema_version": 1,
        "application_mode": "live_gmail_candidate" if not blockers else "live_gmail_blocked",
        "ready_for_live_gmail": not blockers,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "note": "Read-only diagnostics. A passing report is not a security audit and does not enable sending, authorize an account, approve an action, or prove live-provider behavior.",
    }
