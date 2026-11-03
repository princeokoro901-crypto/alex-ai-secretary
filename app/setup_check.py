"""Safe first-run configuration preflight for Alex Core.

This module inspects environment configuration only. It never creates files,
connects to providers, prints secret values, or changes integration flags.
Run with ``python -m app.setup_check``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from app.security import load_api_keys

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _flag(env: Mapping[str, str], name: str, checks: list[dict], default: str = "false") -> bool:
    raw = env.get(name, default).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    checks.append({"id": f"flag:{name}", "status": "error",
                   "detail": f"{name} must be a boolean value such as true or false."})
    return False


def _check(checks: list[dict], check_id: str, status: str, detail: str) -> None:
    checks.append({"id": check_id, "status": status, "detail": detail})


def _path_accessible(value: str | None) -> bool:
    if not value or not value.strip():
        return False
    path = Path(value).expanduser()
    try:
        if path.exists():
            return path.is_file() and os.access(path, os.R_OK | os.W_OK)
        parent = path.parent if str(path.parent) else Path(".")
        return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    except (OSError, ValueError):
        return False


def _safe_redirect_uri(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "https":
            return True
        return parsed.scheme == "http" and parsed.hostname.lower() in {
            "localhost", "127.0.0.1", "::1"
        }
    except ValueError:
        return False


def build_setup_report(env: Mapping[str, str] | None = None) -> dict:
    """Return a value-redacted setup report from a supplied or process environment."""
    env = os.environ if env is None else env
    checks: list[dict] = []

    oauth_enabled = _flag(env, "ALEX_GOOGLE_OAUTH_ENABLED", checks)
    real_email_enabled = _flag(env, "ALEX_REAL_EMAIL_ENABLED", checks)
    gmail_enabled = _flag(env, "ALEX_GMAIL_SENDER_ENABLED", checks)
    allowed = {part.strip() for part in env.get("ALEX_EMAIL_ALLOWED_PROVIDERS", "").split(",") if part.strip()}

    # Validate API-key structure using the application's canonical parser, but
    # never return or print the supplied key material.
    raw_keys = env.get("ALEX_API_KEYS_JSON", "")
    active_admin = False
    if not raw_keys.strip():
        _check(checks, "admin_api_key", "warning" if not gmail_enabled else "error",
               "No API-key configuration was supplied; local development only is appropriate." if not gmail_enabled
               else "Configure ALEX_API_KEYS_JSON with an active administrator key before enabling Gmail.")
    else:
        try:
            entries = load_api_keys(raw=raw_keys)
            active_admin = any(
                entry.get("role") == "admin" and entry.get("active", True)
                and (entry.get("permissions") is None or "admin:all" in entry["permissions"])
                for entry in entries
            )
            _check(checks, "admin_api_key", "pass" if active_admin else ("error" if gmail_enabled else "warning"),
                   "An active administrator API key with administrative permission is configured." if active_admin
                   else "No active administrator key with administrative permission was found.")
        except RuntimeError:
            _check(checks, "admin_api_key", "error",
                   "ALEX_API_KEYS_JSON is malformed; correct its JSON and key entries. Secret values are not shown.")

    if oauth_enabled:
        oauth_fields = (
            "ALEX_GOOGLE_OAUTH_CLIENT_ID", "ALEX_GOOGLE_OAUTH_CLIENT_SECRET",
            "ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY", "ALEX_GOOGLE_OAUTH_STATE_DB_PATH",
            "ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH", "ALEX_GOOGLE_OAUTH_REDIRECT_URI",
        )
        missing = [name for name in oauth_fields if not env.get(name, "").strip()]
        _check(checks, "google_oauth_configuration", "error" if missing else "pass",
               "Missing required Google OAuth configuration: " + ", ".join(missing) if missing
               else "Required Google OAuth configuration values are present; values are not displayed.")
        key_value = env.get("ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY", "").strip()
        if key_value:
            try:
                Fernet(key_value.encode("ascii"))
                _check(checks, "oauth_encryption_key_format", "pass", "The OAuth encryption key has a valid Fernet format.")
            except (ValueError, UnicodeEncodeError):
                _check(checks, "oauth_encryption_key_format", "error", "The OAuth encryption key format is invalid.")
        redirect = env.get("ALEX_GOOGLE_OAUTH_REDIRECT_URI", "").strip()
        if redirect:
            _check(checks, "oauth_redirect_uri", "pass" if _safe_redirect_uri(redirect) else "error",
                   "The redirect URI uses an allowed HTTPS or loopback HTTP form." if _safe_redirect_uri(redirect)
                   else "Use HTTPS for deployed callbacks, or HTTP only for localhost loopback; omit query and fragment.")
    else:
        _check(checks, "google_oauth_configuration", "warning",
               "Google OAuth is disabled; no account connection will be attempted.")

    if gmail_enabled:
        requirements = {
            "ALEX_GMAIL_SENDER_ACTOR_ID": "the configured account owner actor",
            "ALEX_GMAIL_SENDER_ACCOUNT_ID": "the owner-scoped Google account ID",
            "ALEX_GMAIL_SEND_LEDGER_PATH": "persistent Gmail send-ledger storage",
            "ALEX_ORCHESTRATION_STATE_PATH": "persistent orchestration state",
            "ALEX_AUDIT_LOG_PATH": "persistent audit storage",
            "ALEX_APPROVAL_STORE_PATH": "persistent approval storage",
            "ALEX_EXECUTION_JOURNAL_PATH": "persistent execution journal storage",
        }
        missing = [name for name in requirements if not env.get(name, "").strip()]
        if missing:
            _check(checks, "gmail_required_configuration", "error",
                   "Missing required Gmail configuration: " + ", ".join(missing) + ".")
        else:
            _check(checks, "gmail_required_configuration", "pass",
                   "Required Gmail identity and persistent-store settings are present.")

        flags_ok = oauth_enabled and real_email_enabled and "gmail" in allowed
        _check(checks, "gmail_feature_gates", "pass" if flags_ok else "error",
               "OAuth, real-email, and Gmail allowlist gates are enabled." if flags_ok
               else "Gmail requires ALEX_GOOGLE_OAUTH_ENABLED=true, ALEX_REAL_EMAIL_ENABLED=true, and gmail in ALEX_EMAIL_ALLOWED_PROVIDERS.")

        path_names = (
            "ALEX_GMAIL_SEND_LEDGER_PATH", "ALEX_ORCHESTRATION_STATE_PATH",
            "ALEX_AUDIT_LOG_PATH", "ALEX_APPROVAL_STORE_PATH", "ALEX_EXECUTION_JOURNAL_PATH",
            "ALEX_GOOGLE_OAUTH_STATE_DB_PATH", "ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH",
        )
        inaccessible = [name for name in path_names if env.get(name, "").strip() and not _path_accessible(env[name])]
        _check(checks, "persistent_store_paths", "error" if inaccessible else "pass",
               "Storage paths are not accessible or their parent directories are missing: " + ", ".join(inaccessible) + "."
               if inaccessible else "Configured persistent-store paths appear writable without creating files.")
    else:
        _check(checks, "gmail_delivery", "warning",
               "Gmail sender is disabled. Use sandbox.email for non-delivery workflow tests; no external email can be sent by this configuration.")
        if real_email_enabled and "gmail" not in allowed:
            _check(checks, "provider_allowlist", "warning",
                   "The global real-email flag alone does not register or enable a Gmail sender.")

    errors = [item["id"] for item in checks if item["status"] == "error"]
    warnings = [item["id"] for item in checks if item["status"] == "warning"]
    mode = "gmail_preflight" if gmail_enabled else "sandbox_only"
    return {
        "schema_version": 1,
        "target_mode": mode,
        "configuration_valid": not errors,
        "external_email_enabled_by_this_check": False,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "note": "Read-only preflight. It never enables features, creates files, authorizes an account, sends requests, or proves live-provider readiness.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely validate Alex Core first-run environment configuration.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args(argv)
    report = build_setup_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Alex Core — first-run configuration check")
        print(f"Target mode: {report['target_mode']}")
        print(f"Configuration valid: {'yes' if report['configuration_valid'] else 'no'}")
        for item in report["checks"]:
            print(f"[{item['status'].upper()}] {item['id']}: {item['detail']}")
        print(report["note"])
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
