# Alex Core v3.24 — Google OAuth Adapter Foundation

## What changed

- Added `GoogleOAuthAdapter`, disabled by default, for Google's server-side OAuth authorization-code flow.
- Builds a Google authorization URL using the trusted redirect URI configured in `OAuthAuthorizationService`, one-time state, and PKCE S256.
- Requests offline access and the identity plus Gmail-send scopes needed for a future mail-sending integration.
- Exchanges an authorization code only through a server-side transport and includes the stored PKCE verifier, configured client ID/secret, and exact trusted redirect URI.
- Uses a fixed Google OpenID Connect user-info endpoint to verify a stable `sub`, a syntactically plausible email address, and `email_verified=true` before saving tokens.
- Persists access/refresh tokens through the existing encrypted `OAuthCredentialVault`; status output is redacted and the completion result returns only a masked email label.
- Uses a fixed-endpoint `urllib` transport with HTTPS endpoints, redirects disabled, a bounded response size, a timeout, and sanitized errors. The transport is injectable so tests do not need network access or live credentials.
- A failed exchange consumes the one-time state. The user must restart authorization; callback replay is not permitted.

## Validation

`tests/test_google_oauth.py` covers the disabled-by-default gate, authorization URL parameters, PKCE use, token exchange, identity verification, encrypted token storage, actor binding, replay protection, missing scopes, unverified identity, and sanitized provider failures. The test suite uses a fake transport and performs no real Google requests.

## Deliberate limitations

- No Google OAuth client ID/secret is supplied by this project, and no live account was connected or tested.
- This module is not registered in `app/main.py` and no public authorization/callback HTTP routes are added. That avoids exposing the callback before a browser-session, login-CSRF, callback-state, and deployment model has been reviewed.
- The adapter does not automatically refresh tokens, call Google's revocation endpoint, send mail, or implement the Gmail API sender contract. Local credentials are stored only; real sending remains unavailable unless a separate trusted provider adapter is implemented and registered.
- The `gmail.send` scope can trigger Google's consent, verification, and publishing requirements depending on deployment and user population. Configure the OAuth consent screen and exact redirect URI in Google Cloud before any live test.
- Provider-side behavior, real consent, revocation, quota handling, and actual email delivery are unverified. Do not enable this flow in a public deployment without security review.

## Next

Implement a reviewed server-side browser/session callback integration, then a token refresh/revocation lifecycle and a separate Gmail API sender adapter. Keep sending disabled until those parts are tested end-to-end and the existing approval-bound execution gateway remains the only route to external side effects.
