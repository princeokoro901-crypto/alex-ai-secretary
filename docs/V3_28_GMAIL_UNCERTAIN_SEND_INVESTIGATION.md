# v3.28 — Gmail uncertain-send investigation queue

## Change

Adds a safe, account-scoped inventory of unresolved Gmail send-ledger records and an administrator-only endpoint:

`GET /api/v1/connectors/gmail.email/uncertain-sends?limit=100`

The endpoint returns only status, attempt ID, provider message ID (when available), and timestamps. It does not expose account hashes, idempotency keys, payload hashes, message bodies, recipients, or OAuth credentials. The limit must be an integer from 1 to 500. Requests are audit logged with the number of rows returned.

## Safety boundary

This endpoint is an investigation queue, not proof of delivery and not an automated reconciliation mechanism. It does not alter ledger state, retry sends, unlock idempotency keys, or mark executions reconciled. An operator must verify provider-side evidence and separately record an appropriate execution reconciliation. Because Gmail does not offer a general client-supplied send idempotency key, ambiguous attempts must remain blocked until investigated.

The feature is unavailable unless the explicitly configured Gmail sender is enabled. Real sending remains disabled by default. No live Google requests or emails are part of the automated tests.
