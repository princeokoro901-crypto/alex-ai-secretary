# Alex Core v3.8 — Cross-Store Recovery & Lifecycle Reconciliation

## Purpose

v3.8 adds a read-only reconciliation layer between durable orchestration state and the audit log. It detects lifecycle gaps after restart or partial persistence without automatically mutating, deleting, or replaying data.

## Changes

- Added `LifecycleReconciliationService`.
- Added `orchestration_started` audit events before orchestration work begins.
- Added lifecycle classifications:
  - `recoverable`: a lifecycle gap that requires review and may be safely recoverable later.
  - `inconsistent`: durable stores disagree about a completed/replayed orchestration.
- Added `GET /api/v1/reconciliation`.
- Health now reports reconciliation status/counts.
- Existing `/api/v1/consistency` remains unchanged for its narrower v3.7 checks.

## Safety boundary

Reconciliation is diagnostic only. It does not automatically retry, replay, delete, rewrite, or repair orchestration state. No external credentials, services, or approvals are assumed.

## Next

v3.9 should introduce explicit recovery workflows for only the recoverable classifications, with idempotent operator approval before any mutation. After that, proceed to API-key rotation and rate limiting.
