# Alex Core v3.21 — Connector Reliability and Execution History

## Changes

- Added optional `idempotency_key` to the simulation-only `sandbox.email` connector. Keys are restricted to 1–128 ASCII letters, digits, `.`, `_`, `:`, and `-`.
- Bound the idempotency key into the exact action preview using SHA-256, so changing the key changes the action that must be approved.
- Added SQLite uniqueness enforcement for non-null idempotency keys. Replaying the same key with identical recipient/subject/body returns the existing simulated message; reusing it with different content is rejected.
- Added a safe additive SQLite schema migration for v3.20 outbox databases. Existing rows are preserved and receive a null idempotency key.
- Added bounded, status-filtered durable execution history through `GET /api/v1/executions?limit=100&status=succeeded`.
- The history endpoint is administrator-only and requires `ALEX_EXECUTION_JOURNAL_PATH` to be configured. Valid statuses: `running`, `succeeded`, `failed`, `uncertain`; limit: 1–500.

## Configuration

To persist execution history, set `ALEX_EXECUTION_JOURNAL_PATH` to a writable SQLite path, for example `./data/executions.sqlite3`. To persist the simulated email outbox, set `ALEX_SANDBOX_EMAIL_DB_PATH` to a writable SQLite path.

For email simulations, include an `idempotency_key` in the input when repeat submissions should resolve to the same outbox message. The exact action preview must be generated and approved with that same key included. This key is a deduplication mechanism, not an authorization credential.

## Validation

- Automated tests: 125 passed.
- Python compilation: passed.
- Same-key/same-payload replay returns one message.
- Same-key/different-payload reuse is rejected.
- v3.20 SQLite schema migration preserves existing rows.
- Approval action changes when the idempotency key changes.
- Execution history supports bounds and status filtering.
- Execution history endpoint requires administrator permission.

## Limitations

`sandbox.email` remains simulation-only and makes no network calls. Idempotency protects the local outbox from duplicate simulated records; it does not establish end-to-end idempotency for future real email providers. A real provider integration must use provider-specific idempotency/reconciliation behavior where available. Execution history is available only when the optional journal is configured. SQLite journal startup recovery assumes a single active application process per journal path.
