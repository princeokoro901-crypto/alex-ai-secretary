# Alex Core v3.20 — Sandbox Email Connector

## Goal

Prove one complete connector workflow without connecting to an external provider or sending real messages. The `sandbox.email` connector writes simulated messages to a SQLite outbox and labels each result `simulation_only` with `sent: false`.

## Approval binding

`POST /api/v1/connectors/sandbox.email/action-preview` validates the email fields and returns an exact action string. That string includes the recipient and SHA-256 digests of the subject and body. The registry recomputes the string from the execution payload and rejects mismatches **before consuming the single-use permit**. This prevents a caller from approving one email payload and swapping its recipient or content at execution time.

## End-to-end workflow

1. Create a mission with `POST /api/v1/missions`.
2. Preview the exact action using `POST /api/v1/connectors/sandbox.email/action-preview`.
3. Submit that action to `POST /api/v1/missions/{mission_id}/actions`.
4. Have an administrator decide the approval using `POST /api/v1/approvals/{approval_id}/decision`.
5. Issue the one-use permit at `POST /api/v1/approvals/{approval_id}/execution-permit`.
6. Execute through `POST /api/v1/connectors/sandbox.email/execute` with `permit_id`, `mission_id`, the same `action`, and the `input` object.
7. An administrator can inspect simulated messages at `GET /api/v1/sandbox-email/outbox`.

The API middleware protects connector execution with `actions:execute`; the action-preview endpoint uses `connectors:read`; and the outbox is restricted to `admin:all`. Existing role ceilings and explicit API-key permissions still apply.

## Configuration

- `ALEX_SANDBOX_EMAIL_DB_PATH` (optional): path to persist the simulated outbox. When unset, the outbox is in-memory and messages disappear when the process stops.
- No email credentials, SMTP settings, or network integration are used.

## Validation

Tests cover message validation, SQLite persistence, payload/action binding, the API approval flow, tampering rejection, permit preservation after a pre-execution mismatch, and administrator-only outbox access. The test suite runs with `PYTHONPATH=. python -m pytest -q`.

## Limitations / next steps

This is a local prototype, not a real email integration. The SQLite outbox is not a mail queue and has no delivery, bounce, unsubscribe, attachment, OAuth, provider idempotency, or rate-limit behavior. Before a real provider is added, implement provider credential handling, explicit send confirmation policy, provider idempotency/reconciliation, delivery status tracking, and integration tests against a provider sandbox.
