# v3.27 — Approval-bound Gmail sending integration

## What changed

- Added a Google Gmail API send operation using a fixed HTTPS endpoint and a bearer token retrieved only inside trusted server-side adapter code.
- Added a MIME message builder that validates the recipient, subject, body, and idempotency key and rejects subject line breaks to prevent header injection.
- Added `GoogleGmailEmailProvider`, registered only when the operator explicitly enables the sender and supplies a connected-account owner, stable Google account ID, and durable send-ledger path.
- Registered the real `gmail.email` connector through the existing `ConnectorRegistry` and `ExecutionGateway`. The connector is administrator-only and requires the existing single-use, exact-action-bound execution permit.
- Added an action-aware handler option to the gateway/connector boundary so the handler receives the exact action consumed by the permit; it rechecks the action against current provider/account/message state immediately before the send.
- Added a durable SQLite send ledger keyed by account hash and idempotency-key hash. It stores payload hashes and provider receipt IDs, not message bodies or OAuth tokens. A confirmed successful replay returns the prior receipt without a second Gmail API call; key reuse with different content is rejected.
- Added an explicit uncertain execution outcome. If the Gmail API call fails ambiguously or the receipt cannot be safely recorded, the execution journal records `uncertain`, the send ledger blocks a repeat attempt, and Alex does not automatically resend.
- Added `POST /api/v1/connectors/gmail.email/action-preview`. It is admin-only, disabled when the sender is not configured, and performs no external send.

## Configuration gates

Real sending remains off by default. To opt into the Gmail sender in a protected deployment, configure all of the following:

- `ALEX_GOOGLE_OAUTH_ENABLED=true` plus valid `ALEX_GOOGLE_OAUTH_CLIENT_ID`, `ALEX_GOOGLE_OAUTH_CLIENT_SECRET`, `ALEX_GOOGLE_OAUTH_ENCRYPTION_KEY`, `ALEX_GOOGLE_OAUTH_STATE_DB_PATH`, `ALEX_GOOGLE_OAUTH_CREDENTIAL_DB_PATH`, and the exact registered `ALEX_GOOGLE_OAUTH_REDIRECT_URI`.
- `ALEX_REAL_EMAIL_ENABLED=true`.
- `ALEX_EMAIL_ALLOWED_PROVIDERS=gmail` (or an allowlist containing `gmail`).
- `ALEX_GMAIL_SENDER_ENABLED=true`.
- `ALEX_GMAIL_SENDER_ACTOR_ID` matching the actor that owns the linked Google account.
- `ALEX_GMAIL_SENDER_ACCOUNT_ID` matching that account's stable Google `sub` identifier.
- `ALEX_GMAIL_SEND_LEDGER_PATH` pointing to durable storage.
- Persistent `ALEX_APPROVAL_STORE_PATH`, `ALEX_AUDIT_LOG_PATH`, and `ALEX_EXECUTION_JOURNAL_PATH` values.

If required configuration is missing, startup fails closed rather than partially enabling real delivery. The sender is not registered just because the general email flag is true. Keep the database files and Fernet encryption key protected and backed up; do not place secrets in source control or request payloads.

## Approved workflow

1. Complete Google authorization for the configured account through the existing browser-bound OAuth flow.
2. Create or select an existing mission.
3. Call `POST /api/v1/connectors/gmail.email/action-preview` with `to`, `subject`, `body`, and `idempotency_key`. This returns a content/account-bound action string but sends nothing.
4. Propose that exact action through the existing mission action/approval route.
5. Have an administrator approve it, then issue a single-use execution permit for the same mission and exact action.
6. Execute via `POST /api/v1/connectors/gmail.email/execute`, passing `permit_id`, `mission_id`, `action`, and the same message under `input`.
7. Inspect execution history and reconcile any `uncertain` result before taking further action.

The approval string contains SHA-256 digests rather than the recipient or message body. The idempotency key is also bound into the action.

## Important delivery semantics

Gmail's send API does not offer a general idempotency key for message creation. The local ledger prevents Alex from issuing a second API call for a previously used key, but it cannot make Gmail and SQLite one atomic transaction. If a process crashes after Google accepts a message but before Alex records the receipt, the key remains unresolved and must not be retried. An administrator can investigate Gmail and record the execution reconciliation, but reconciliation does not automatically unlock the local send key. A genuinely new attempt requires a new idempotency key, a new exact action, and a new approval after the previous outcome has been investigated.

A successful Gmail API receipt means Google accepted the message for sending; it does not prove the recipient's mailbox received it.

## Validation and limitations

Automated tests use fake OAuth/Gmail transports and make no live Google requests. They cover MIME construction, fixed endpoint use, account/action binding, approval-gateway integration, duplicate replay, conflicting-key rejection, header-injection rejection, and uncertain-outcome journaling. No live Google account, client secret, or external email send was used in this development run.

Before enabling this feature for real users, perform a production security review, test with a dedicated low-risk account, validate Google OAuth consent/scope requirements, configure TLS/reverse-proxy headers and secret redaction, and rehearse uncertain-send reconciliation. The application still assumes one active process per execution-journal path; the Gmail send ledger is durable and SQLite-coordinated, but the whole app is not yet certified for multi-worker production deployment.
