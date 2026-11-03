# v3.62 — Durable reconciliation audit recovery

## Change

Execution reconciliation records now carry durable `audit_status` and stable `audit_event_id` fields in the SQLite execution journal. Older journal schemas are upgraded in place without discarding existing decisions. Reconciliation starts as `pending`; after the append-only audit event is durably written, the journal marks it `recorded`.

If audit append or the status update fails, the reconciliation remains visible in the administrator-only pending queue. An administrator can retry audit delivery without repeating the reconciliation decision or any external action. The stable audit event ID makes a retry idempotent if the audit append succeeded but the SQLite status update failed.

Endpoints:

- `GET /api/v1/executions/reconciliation-audit/pending` — administrator-only pending queue.
- `POST /api/v1/executions/reconciliations/{reconciliation_id}/audit/retry` — administrator-only retry for audit delivery only.

Both ordinary execution reconciliation and the Gmail-specific reconciliation path use the same durable audit outbox workflow. This does not change execution status, unblock a Gmail send, or authorize retries.

## Validation

`tests/test_v362_reconciliation_audit_recovery.py` injects audit and status-update failures, confirms pending-state persistence, retries delivery, verifies stable event IDs prevent duplicate audit events, checks administrator-only visibility, and upgrades a legacy journal schema without losing decisions.

All tests use isolated local temporary SQLite stores and a fake audit sink. No live provider or external action is invoked. Production deployment and multi-process safety are not established by these tests.
