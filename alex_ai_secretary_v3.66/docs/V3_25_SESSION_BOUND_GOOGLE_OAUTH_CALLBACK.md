# Alex Core v3.25 — Session-Bound Google OAuth Callback

## Summary

v3.25 adds a server-side browser callback flow for the existing Google OAuth adapter. It is disabled by default and requires explicit operator configuration. The route is for linking a Google identity and storing encrypted credentials; it does **not** send email.

## Flow

1. An administrator calls `POST /api/v1/email/google/oauth/start` using Alex's normal API-key authentication.
2. Alex creates a cryptographically random browser-binding value if the browser does not already have one. The value is returned only as a short-lived `HttpOnly`, `SameSite=Lax` cookie (and is `Secure` when the configured callback URI uses HTTPS).
3. The state service stores only a SHA-256 digest of the binding cookie, alongside a hash of OAuth state, provider, exact configured redirect URI, initiating actor/key IDs, expiry, and encrypted PKCE verifier.
4. The browser visits the returned Google authorization URL.
5. Google returns to `GET /oauth/google/callback`. The callback rejects duplicate `state`, `code`, or `error` query parameters and requires both the one-time state and the same browser cookie.
6. The callback consumes state atomically, retrieves actor/key identity only from the server-side record, exchanges the code server-side using PKCE, verifies the Google identity and required scopes, and saves tokens through the encrypted vault.
7. The callback redirects to a fixed local landing path. Query values and provider error details are not reflected in the landing page.

A provider denial also consumes the state, preventing a later replay. Failed code exchange consumes state before contacting Google; restart authorization rather than reusing a callback.

## Actor ownership

The credential vault now records the initiating actor as the owner of each provider/account pair. A different actor cannot retrieve, revoke, or overwrite that account's credentials through the vault. Existing credential rows from older schemas have no owner and are therefore not visible to any actor; reauthorize those accounts to create an owner-bound record.

The browser-binding cookie is **not** an authentication credential. The start endpoint remains API-key protected and administrator-only. The callback depends on the unguessable state plus the short-lived cookie and the server-side transaction record.

## Configuration

The integration is off unless `ALEX_GOOGLE_OAUTH_ENABLED=true`. When enabling it, configure all of the following in the deployment environment (not in source control):

- `ALEX_GOOGLE_OAUTH_CLIENT_ID`
- `ALEX_GOOGLE_OAUTH_CLIENT_SECRET`
- `ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY` — a Fernet key generated with `cryptography.fernet.Fernet.generate_key()`
- `ALEX_GOOGLE_OAUTH_STATE_DB_PATH` — durable writable SQLite path
- `ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH` — durable writable SQLite path
- `ALEX_GOOGLE_OAUTH_REDIRECT_URI` — exact registered URI ending in `/oauth/google/callback`

If the feature flag is enabled and any required value is missing, application startup fails closed. Register the exact redirect URI in the Google Cloud OAuth client. Production must use HTTPS. HTTP is accepted only for localhost development by the existing redirect validator. Protect the encryption key and SQLite files with restrictive filesystem permissions, backups, and a key-rotation plan.

Example key-generation command (run locally; do not commit the output):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Security and operational notes

- The start route is `admin:all` protected. Do not expose Alex in local-development mode without configuring API keys and a secure deployment boundary.
- Use HTTPS in production so the browser-binding cookie receives the `Secure` attribute.
- Configure reverse proxies and application logs to redact OAuth callback query strings. They can contain authorization codes and state values.
- The callback uses fixed internal redirects; no user-supplied redirect destination is accepted.
- The landing page is intentionally generic and does not confirm authorization success. There is not yet a user-facing connected-account status page.
- OAuth state and credentials are persisted in separate databases. Use durable local storage and back them up securely.
- Existing `owner_actor_id`-less credential rows are intentionally hidden after migration. Reauthorize them rather than guessing an owner.
- No live Google credentials were supplied or tested in development. Automated tests use a fake transport and make no external requests.

## Remaining work

- Build an authenticated connected-account status and disconnect interface.
- Implement access-token refresh and provider-side revocation.
- Add a Gmail sender adapter integrated with `EmailProviderRegistry` and the existing approval-bound `ExecutionGateway`.
- Test the complete real-provider workflow with an operator-supplied test account before enabling delivery.
- Review session behavior, reverse-proxy logging, database backup/permissions, encryption-key rotation, and deployment topology before production use.

Real email sending remains unavailable in this release.
