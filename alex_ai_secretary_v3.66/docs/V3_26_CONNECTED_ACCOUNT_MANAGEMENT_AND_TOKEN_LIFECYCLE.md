# Alex Core v3.26 — Connected Account Management and Token Lifecycle

## Goal

Build the next safe step after the v3.25 browser-bound Google OAuth callback: owner-scoped connected-account management, refresh coordination, and disconnect behavior. This release does not add Gmail message sending.

## Changes

- Added a redacted masked account label to the OAuth credential vault, with a migration for existing SQLite databases. The full email address is not persisted as the display label.
- Added `GET /api/v1/email/google/accounts`. It lists only accounts owned by the authenticated actor and returns account metadata, scope names, expiry, authorization state, and refresh status—not access or refresh tokens.
- Added `POST /api/v1/email/google/accounts/{account_id}/refresh`. It refreshes only when the token is within the 60-second safety window, validates the returned bearer token and expiry, retains previously granted scopes if the refresh response omits them, and preserves the existing refresh token unless the provider rotates it.
- Added a SQLite-backed refresh lease. Only one worker can claim a refresh at a time; expired leases can be reclaimed after a crash, and stale workers cannot overwrite the latest token.
- Added a fixed-endpoint HTTPS Google token-revocation request to the transport.
- Added `POST /api/v1/email/google/accounts/{account_id}/disconnect`. It attempts provider-side revocation using the refresh token when available, then disables local credentials even if the external request fails. The response explicitly distinguishes local disconnection from provider revocation.
- Added audit events for refresh and disconnect attempts without recording token values. All account-management routes require `admin:all`; each vault operation also enforces the owning actor.

## Configuration and operation

The routes are available only when the existing `ALEX_GOOGLE_OAUTH_ENABLED=true` configuration has all required Google OAuth settings. Google OAuth remains disabled by default. The account identifier in the path is the provider's stable account subject returned by the redacted account inventory.

- `GET /api/v1/email/google/accounts` — list the current actor's connected accounts.
- `POST /api/v1/email/google/accounts/{account_id}/refresh` — refresh an expiring token if required; returns `refresh_performed=false` when the token is not close to expiry. A conflict/failure returns HTTP 409 with a generic message.
- `POST /api/v1/email/google/accounts/{account_id}/disconnect` — disable local credentials and attempt remote revocation. A `provider_revocation=failed` result means local use is disabled, but the operator should verify/revoke the grant in Google account settings.

## Safety details and limitations

- No access or refresh token is returned from HTTP endpoints or included in audit details.
- Refresh failures release the lease while preserving the previously stored credential material. An expired access token with a refresh token remains marked as needing refresh; this is not evidence that a refresh has succeeded.
- A refresh response is committed only if the caller still owns the active lease and the account remains active. A concurrent disconnect or newer refresh prevents stale completion.
- Local disconnect always clears the active encrypted token fields and lease. Provider revocation is best-effort; a network failure can leave the external grant active. SQLite WAL/backup remnants are not guaranteed to be forensically erased.
- This milestone has not been tested against Google's live endpoints, and no Google credentials were supplied. Tests use fake transports.
- No Gmail sender is registered with `EmailProviderRegistry` or `ExecutionGateway`; this release cannot send real email.
- Before production, review deployment behind HTTPS, API key lifecycle, rate limiting across workers, SQLite file permissions/backups, encryption-key rotation, proxy/access-log redaction, and provider-side revocation behavior.

## Validation

See `TEST_RESULTS.txt` for the test suite and packaged-archive validation.

## Next step

Implement the Gmail send adapter only after provider lifecycle behavior is reviewed. It must validate the recipient/message, bind the provider account and exact message to the approval action, execute through the existing single-use permit and controlled gateway, and treat ambiguous outcomes as uncertain without automatic retries.
