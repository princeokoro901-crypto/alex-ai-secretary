# Alex Core v3.15 — Approval-Bound Execution Permits

## Summary

v3.15 strengthens the v3.14 human-approval workflow by binding each approval to a SHA-256 digest of the exact mission ID and normalized action description. Once approved, an admin or operator can request one execution permit for that exact action. The permit can be consumed only once, and only when the mission/action pair still matches.

## New behavior

- Approval records store `action_digest` plus permit issuance and consumption metadata.
- Older v3.14 records are migrated in memory when loaded: a missing digest is calculated from the stored mission/action pair. A digest that exists but conflicts with the stored action causes the store to fail closed.
- `POST /api/v1/approvals/{approval_id}/execution-permit` issues a permit only for an approved, exact-match proposal. It requires `actions:execute`, available to admin and operator roles by default.
- `ApprovalService.consume_execution_permit(...)` is an atomic, single-use guard intended for a future connector/executor to call immediately before a side effect. It checks actor role, permit existence, approval status, exact mission/action binding, and replay status, then persists who consumed the permit.
- Audit events record permit issuance with actor ID, key ID, mission ID, action digest, and permit ID. They do not include raw API keys or action text.

## Important boundary

This release does **not** send emails, modify documents, make calls, or perform any external side effect. Issuing a permit is not execution. No external executor is wired yet. A future connector must call `consume_execution_permit` at the side-effect boundary and must not perform the action if validation fails. Because external side effects and local JSON persistence cannot be one atomic transaction, future connectors also need idempotency/reconciliation handling for failures between permit consumption and the external operation.

## Persistence and security notes

- Approval records remain in-memory unless `ALEX_APPROVAL_STORE_PATH` is configured.
- The approval JSON file and audit log are separate stores; a crash can occur between a state change and its audit record. Reconciliation/transactional outbox work remains future scope.
- JSON file storage is suitable for the current foundation/tests, not multi-process concurrency without external locking or a database.
- Local-development mode remains enabled when `ALEX_API_KEYS_JSON` is unset; do not expose that mode to untrusted networks.
- Production still requires TLS, secret management, backups, shared rate limiting, deployment hardening, and a security review.

## Validation

Automated coverage includes exact action/mission binding, pending-approval denial, duplicate permit issuance denial, one-time consumption, replay rejection, persistence reload, v3.14 record migration, endpoint role restrictions, and audit attribution, alongside the existing suite.
