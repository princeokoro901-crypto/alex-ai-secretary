# v3.30 — Operational readiness diagnostics

## Purpose

Provide a safe, administrator-only snapshot of deployment prerequisites before any operator considers enabling real Gmail delivery. This is a diagnostics endpoint, not a setup wizard, security audit, or automatic enablement mechanism.

## Changes

- Adds `GET /api/v1/system/readiness`, restricted to administrators by both the API permission map and route-level role check.
- Reports whether API-key configuration contains an active administrator key; whether orchestration, audit, approval, execution-journal, OAuth state, encrypted credential-vault, and Gmail send-ledger storage are configured and accessible; whether the audit chain verifies; and whether the Gmail feature flags, provider allowlist, and owner-scoped account authorization are ready.
- Returns safe check IDs, statuses, blocker/warning counts, and explanatory text only. It never returns API keys, OAuth tokens, account identifiers, configured filesystem paths, message content, or provider payloads.
- Records a metadata-only audit event for each readiness inspection.
- Keeps `/health` public and minimal, updates the release version to 3.30, and preserves the existing fail-closed Gmail configuration and approval-bound execution path.

## Interpretation

`ready_for_live_gmail` means the configured checks passed at the time of inspection. It is not proof of a successful live Google API call, a production security audit, or authorization to send a specific message. The endpoint does not enable OAuth or Gmail, approve an action, connect an account, change a send ledger, or perform network requests. A passing check cannot replace a controlled end-to-end test with an explicitly authorized account.

No API keys or OAuth credentials are supplied by this release. The test suite uses temporary files and fake service objects; no live Google requests or external emails are performed.

## Validation

- Baseline v3.29 suite: 199 tests passed.
- v3.30 suite: 204 tests passed.
- Readiness blocking, persistence checks, authorization state, and sensitive-value/path redaction: passed.
- Administrator-only endpoint access: passed; operator access rejected.
- Python compilation and packaged-archive tests: validated for the release archive.
