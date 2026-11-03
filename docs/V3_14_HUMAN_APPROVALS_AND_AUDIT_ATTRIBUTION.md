# Alex Core v3.14 — Human Approvals and Audit Attribution

## Delivered

- Added an approval service with `pending`, `approved`, and `rejected` states.
- Proposed mission actions now create a pending approval record. The response explicitly states that creating an approval record does **not** execute an action.
- A requester cannot approve or reject their own request, even when using a different API key with the same actor ID.
- Rejections require a reason; only pending records can receive a decision.
- Approval state can be persisted atomically to JSON using `ALEX_APPROVAL_STORE_PATH`. The store uses a temporary file, `fsync`, and atomic replacement. The temporary file is best-effort restricted to owner read/write permissions.
- Corrupt or unsupported approval state fails closed at startup rather than silently resetting the records.
- Added endpoint permissions `approvals:read` and `approvals:decide`. These are admin-only by default; operators can propose actions but cannot decide approvals unless a future explicit role-policy change grants it.
- Orchestration lifecycle audit events now use the authenticated actor ID and include key ID and role. Approval request/decision events include actor and key identifiers without recording raw API keys or the full proposed action in the audit event.
- Health output identifies the release, whether approval persistence is configured, and the pending approval count.

## Configuration

Set `ALEX_APPROVAL_STORE_PATH` to a durable, access-controlled path, for example `data/approvals.json`. If unset, approval state is in memory only and will not survive process restart. Configure `ALEX_AUDIT_LOG_PATH` separately if audit events must survive restart. These stores are not a single cross-store transaction: a disk failure between writing approval state and its audit event can produce an incomplete audit trail, so monitor storage failures and retain backups.

Continue to configure `ALEX_API_KEYS_JSON`; keep production secrets in a secret manager or protected deployment environment. Do not expose local-development mode to untrusted networks. Use TLS and network controls in deployment.

## Safety boundary

The approval workflow currently records a human decision; it does not execute external actions. There is no general-purpose action-execution endpoint in this release. Before connecting email, messaging, file deletion, payments, outbound calls, or other consequential tools, each executor must check the approval record and reject pending/rejected actions at the execution boundary. Do not treat an `approved` record alone as permission to bypass connector-specific authorization, user confirmation, or provider-side safeguards.

The separation rule compares actor IDs, not merely key IDs. In a production identity model, configure actor IDs to represent real distinct people; otherwise, multiple keys assigned to the same person could undermine the intended two-person control.

## Validation

Tests cover persistence/reload, corruption fail-closed behavior, self-approval prevention, rejection reasons, single-decision transitions, endpoint authorization, authenticated actor/key attribution, and hash-chain integrity.

## Remaining work

1. Add an approval decision policy configurable by action risk and required approver role.
2. Make audit/approval persistence more resilient to cross-store failures, ideally with a transactional store or outbox.
3. Add an explicit executor gate that validates approval state and binds approval to an immutable action digest before any external side effect.
4. Add approval expiry, cancellation, idempotency, notification, and UI review screens.
5. Use a shared transactional backend for multi-worker production deployments; the current JSON store is intended for a single-process deployment.
6. Run full threat modelling, deployment security review, and end-to-end tests before production use.
