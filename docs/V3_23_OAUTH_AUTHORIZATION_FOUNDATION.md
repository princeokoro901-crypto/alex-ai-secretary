# Alex Core v3.23 — OAuth Authorization and Credential-Safety Foundation

## What changed

- Added `OAuthAuthorizationService` for persistent OAuth authorization attempts.
- Generates high-entropy, one-time state and PKCE S256 challenge/verifier pairs.
- Persists only a SHA-256 digest of state; the PKCE verifier is encrypted at rest with Fernet.
- Binds each attempt to a configured provider, trusted redirect URI, actor ID, and API-key ID.
- Validates callback context and expiry, and atomically consumes state once to prevent replay.
- Adds `OAuthCredentialVault` to encrypt access/refresh tokens at rest, record scopes and expiry, preserve a refresh token when a provider omits a replacement, and disable local credentials on revocation.
- Token values are available only through an explicitly named trusted-adapter retrieval method. Status output never includes token values.
- Requires the trusted provider adapter to confirm account identity before credentials can be stored.
- Adds `cryptography` as an explicit dependency and bumps the health/application version to 3.23.

## Configuration and usage boundaries

- Generate an encryption key in a secure environment using `cryptography.fernet.Fernet.generate_key()` and keep it in a deployment secret manager or protected environment variable. Never commit it to source control or include it in API responses/logs.
- Use a durable, access-controlled SQLite file path. The service deliberately rejects `:memory:` because authorization state and credentials must persist across requests/restarts.
- `redirect_uris` is trusted application configuration. Do not accept callback destinations from request payloads. HTTPS is required except for localhost development.
- The state TTL is limited to 60–1800 seconds; the default is 600 seconds.
- `consume_callback()` is an internal server-side primitive. Its returned PKCE verifier must never be sent to a browser, returned from an API route, or written to logs.
- `get_tokens_for_adapter()` is a trusted-code API only. It must never be exposed through an HTTP endpoint.
- `identity_verified=True` is an assertion made by trusted provider adapter code only after it verifies the account identity using the provider's authenticated identity endpoint. It is not a user-supplied API field.

## Validation

See `TEST_RESULTS.txt`. Tests cover PKCE challenge correctness, state hashing, encrypted verifier storage, one-time state consumption, actor/key/provider/redirect binding, expiry, redirect validation, encrypted token storage, expiry status, refresh-token preservation, revocation, and secret redaction.

## Deliberate limitations

- No concrete Gmail or Microsoft 365 adapter is included.
- This release does not build authorization URLs, perform browser redirects, exchange authorization codes, call provider identity endpoints, refresh tokens over HTTP, or send email.
- The module is not exposed through HTTP routes yet. A real callback needs a deliberate browser/session and CSRF model; simply adding a public callback endpoint to the current bearer-key API would be unsafe.
- Local revocation clears token ciphertext from the active database row, but it does not revoke the grant at the provider and does not guarantee forensic erasure of old SQLite/WAL pages.
- Encryption-key rotation, backup protection, database file permissions, shared-store coordination, and production secret management remain deployment responsibilities.
- Access-token expiry is reported; automatic refresh is not implemented. A future adapter must refresh safely, verify the resulting account identity, and avoid logging tokens.
- OAuth provider consent scopes and exact redirect URI registration must be verified separately for each provider before a live integration is enabled.

## Next

Implement a provider-specific OAuth adapter in a disabled-by-default integration test environment. It must use this state/PKCE service, exchange codes only server-side, verify the resulting account identity, persist tokens through the vault, and preserve Alex's exact approval-bound execution path. Do not enable real email sending until that complete path has security review and provider-specific tests.
