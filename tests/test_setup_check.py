import json
from cryptography.fernet import Fernet

from app.setup_check import build_setup_report, main


def _statuses(report):
    return {item["id"]: item["status"] for item in report["checks"]}


def test_default_setup_is_sandbox_only_and_never_claims_live_email():
    report = build_setup_report({})
    assert report["target_mode"] == "sandbox_only"
    assert report["configuration_valid"] is True
    assert report["external_email_enabled_by_this_check"] is False
    assert _statuses(report)["gmail_delivery"] == "warning"


def test_invalid_api_key_json_is_reported_without_echoing_secret():
    secret = "never-print-this-key"
    report = build_setup_report({"ALEX_API_KEYS_JSON": secret})
    assert report["configuration_valid"] is False
    assert "admin_api_key" in report["errors"]
    assert secret not in json.dumps(report)


def test_gmail_enablement_fails_closed_when_required_configuration_is_missing():
    report = build_setup_report({
        "ALEX_GMAIL_SENDER_ENABLED": "true",
        "ALEX_GOOGLE_OAUTH_ENABLED": "false",
        "ALEX_REAL_EMAIL_ENABLED": "true",
        "ALEX_EMAIL_ALLOWED_PROVIDERS": "",
    })
    assert report["target_mode"] == "gmail_preflight"
    assert report["configuration_valid"] is False
    assert "gmail_required_configuration" in report["errors"]
    assert "gmail_feature_gates" in report["errors"]


def test_invalid_boolean_flag_is_an_error():
    report = build_setup_report({"ALEX_GMAIL_SENDER_ENABLED": "probably"})
    assert report["configuration_valid"] is False
    assert "flag:ALEX_GMAIL_SENDER_ENABLED" in report["errors"]


def test_oauth_encryption_key_and_redirect_are_validated_without_echoing_values(tmp_path):
    key = Fernet.generate_key().decode("ascii")
    values = {
        "ALEX_GOOGLE_OAUTH_ENABLED": "true",
        "ALEX_GOOGLE_OAUTH_CLIENT_ID": "client-id-secret-ish",
        "ALEX_GOOGLE_OAUTH_CLIENT_SECRET": "client-secret-not-to-print",
        "ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY": key,
        "ALEX_GOOGLE_OAUTH_STATE_DB_PATH": str(tmp_path / "state.sqlite3"),
        "ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH": str(tmp_path / "vault.sqlite3"),
        "ALEX_GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/oauth/google/callback",
    }
    report = build_setup_report(values)
    statuses = _statuses(report)
    assert statuses["oauth_encryption_key_format"] == "pass"
    assert statuses["oauth_redirect_uri"] == "pass"
    serialized = json.dumps(report)
    for secret in ("client-id-secret-ish", "client-secret-not-to-print", key, str(tmp_path)):
        assert secret not in serialized


def test_deployed_redirect_uri_rejects_plain_http_and_query_parameters():
    report = build_setup_report({
        "ALEX_GOOGLE_OAUTH_ENABLED": "true",
        "ALEX_GOOGLE_OAUTH_CLIENT_ID": "client-id",
        "ALEX_GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
        "ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "ALEX_GOOGLE_OAUTH_STATE_DB_PATH": "/tmp/state.sqlite3",
        "ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH": "/tmp/vault.sqlite3",
        "ALEX_GOOGLE_OAUTH_REDIRECT_URI": "http://example.test/callback?token=bad",
    })
    assert _statuses(report)["oauth_redirect_uri"] == "error"


def test_cli_returns_nonzero_when_report_contains_errors(monkeypatch, capsys):
    monkeypatch.setenv("ALEX_GMAIL_SENDER_ENABLED", "true")
    monkeypatch.setenv("ALEX_GOOGLE_OAUTH_ENABLED", "false")
    monkeypatch.setenv("ALEX_REAL_EMAIL_ENABLED", "false")
    assert main(["--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["configuration_valid"] is False
    assert output["external_email_enabled_by_this_check"] is False
