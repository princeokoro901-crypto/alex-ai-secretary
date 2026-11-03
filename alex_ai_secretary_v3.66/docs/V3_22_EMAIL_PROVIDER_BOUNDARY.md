# Alex Core v3.22 — Provider-neutral email integration boundary

## Changes

- Added `EmailProviderRegistry`, a provider-neutral contract for trusted application-code adapters.
- Added a redacted provider readiness inventory at `GET /api/v1/email/providers`; the endpoint is administrator-only and cannot send messages.
- Added exact approval-action construction that binds the provider ID, stable provider account ID (hashed), recipient (hashed), subject/body hashes, and required idempotency key hash.
- Added fail-closed delivery gates: global sending flag, provider allowlist, configured adapter, authorized account, stable account ID, mandatory idempotency key, and exact approved-action match.
- Provider send exceptions and incomplete receipts are treated as ambiguous. The framework does not automatically retry them.
- Provider status output is allowlisted/redacted; arbitrary adapter fields such as access and refresh tokens are not returned.
- No concrete provider adapter is included or registered. The existing `sandbox.email` connector remains simulation-only.

## Configuration

- `ALEX_REAL_EMAIL_ENABLED` must equal the string `true` (case-insensitive) before the provider registry permits delivery.
- `ALEX_EMAIL_ALLOWED_PROVIDERS` is a comma-separated list of provider IDs, for example `gmail,microsoft365`.
- Both settings are necessary but not sufficient: a provider adapter must also be registered by trusted application code and report a valid authorized account.
- Provider adapters must obtain and refresh credentials through a trusted credential source; they must never accept access/refresh tokens from API payloads, log tokens, or include tokens in returned metadata.
- Never expose credential values in this configuration or commit them to source control.

## Validation

- Automated tests: see `TEST_RESULTS.txt`.
- Provider status redaction, feature-flag gating, allowlist gating, authorization checks, action binding, idempotency-key requirement, and ambiguous failure behavior are covered by tests.
- The API inventory is verified to be administrator-only.

## Important limitations

This is an integration boundary, not a working Gmail/Microsoft 365 integration. No OAuth consent flow, token storage/refresh implementation, provider-specific API client, or external network call is included. Enabling the environment flag alone cannot send mail because no adapter is registered by default. The provider registry is not yet wired as an executable connector in `ConnectorRegistry`; that integration must preserve the exact approved-action context through the controlled execution gateway and be reviewed before external sending is enabled.

Provider-level idempotency and reconciliation still depend on each provider adapter. If a provider request times out after acceptance, do not blindly retry; reconcile using provider-specific message status or evidence first.
