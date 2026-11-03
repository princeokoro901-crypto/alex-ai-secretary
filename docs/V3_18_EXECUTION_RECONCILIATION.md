# Alex Core v3.18 — Execution Reconciliation

## Purpose

v3.17 introduced durable execution records and marks an interrupted `running` execution as `uncertain` on startup. v3.18 adds a guarded, auditable way for an administrator to record what is known about an uncertain outcome without pretending that Alex can infer whether an external side effect occurred.

## What changed

- Added append-only SQLite `execution_reconciliations` records with outcome, required reason, actor ID, key ID, optional external reference, and timestamp.
- Allowed outcomes: `confirmed_succeeded`, `confirmed_failed`, and `still_unknown`.
- Only an authenticated `admin` role may record a reconciliation. A non-empty reason and an existing `uncertain` execution are required.
- The original execution status remains `uncertain`. Reconciliation does not rewrite the execution history, execute a handler, reissue a permit, or make retry safe.
- Added endpoints, available only when `ALEX_EXECUTION_JOURNAL_PATH` is configured:
  - `GET /api/v1/executions/uncertain`
  - `GET /api/v1/executions/{execution_id}/reconciliations`
  - `POST /api/v1/executions/{execution_id}/reconcile`
- Reconciliation routes require `admin:all`; operator and reader keys cannot access them.

## Example request

```json
{
  "outcome": "confirmed_failed",
  "reason": "The provider dashboard shows a rejected request; no transaction reference was created.",
  "external_reference": "provider-case-123"
}
```

Send this to `POST /api/v1/executions/{execution_id}/reconcile` using an authenticated administrator key. Do not include API keys in source files or logs.

## Important limitations

- This is a manual adjudication/audit framework, not an automated connector status checker. A human operator must obtain evidence from the external provider before choosing a confirmed outcome.
- No automatic retry path is added. Even `confirmed_failed` does not authorize retry; retry safety requires a separate policy and connector-specific idempotency guarantees.
- The execution status stays `uncertain`; the latest interpretation is represented in the reconciliation history.
- Execution journal startup recovery currently assumes one active application process per journal database path. Multi-worker or multi-instance production deployments need ownership/lease-based recovery so one process cannot misclassify another live process's `running` row.
- The execution journal and the general audit log are separate stores; recording reconciliation and writing the general audit event are not one atomic transaction.
- No external connectors are registered by default. Do not expose a development deployment publicly without proper credentials, TLS, secret management, monitoring, and a deployment-specific security review.
