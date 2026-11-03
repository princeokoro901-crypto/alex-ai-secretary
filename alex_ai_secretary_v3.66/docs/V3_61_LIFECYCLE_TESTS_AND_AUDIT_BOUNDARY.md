# v3.62 — Approval, permit, and reconciliation lifecycle tests

## Covered locally

- Approval decision and execution-permit issuance survive an `ApprovalService` restart.
- Permit consumption survives restart and retains actor/key attribution.
- A reader cannot consume a permit; invalid action binding, duplicate issuance, and duplicate consumption are rejected without changing the approval record.
- Execution-journal reconciliation rejects invalid outcomes and refuses a second conflicting decision for an execution already reconciled.
- A local HTTP test deliberately simulates an audit append failure during reconciliation.

## Known issue found by the boundary test

The current reconciliation route writes the reconciliation row to SQLite and then appends a separate audit event. These are not one atomic transaction. If the audit append fails, the route returns HTTP 503 but the SQLite reconciliation row remains. This can look like a failed request even though part of the decision was persisted. The test makes this behavior visible rather than pretending the stores are atomic.

Before production use, introduce an explicit durable state/recovery protocol and idempotency strategy across the journal and audit append. Do not blindly retry or infer that an external action was or was not performed. The local test uses an isolated temporary SQLite journal and does not call external providers.

## Not validated

No live provider, real external action, normal desktop browser networking, production TLS/proxy, multi-worker rate limiting, or off-device backup destination was exercised.
