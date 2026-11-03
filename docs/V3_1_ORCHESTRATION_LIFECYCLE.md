# Alex v3.1 — Orchestration Lifecycle & Idempotency

## Purpose
Make end-to-end orchestration safely repeatable and inspectable without changing the workforce authority model.

## Changes
- Optional `idempotency_key` on orchestration requests.
- Repeating a request with the same key returns the original orchestration result instead of creating a second mission execution.
- Added `GET /api/v1/orchestrations/{orchestration_id}` for lifecycle inspection.
- Unknown orchestration IDs return 404.

## Safety
Idempotency is a deduplication mechanism, not an authorization mechanism. Routing remains advisory and the runtime continues to re-check workforce, mission, tool, budget, and execution boundaries.

## Next
Persist orchestration records and idempotency keys so deduplication and lifecycle inspection survive process restarts. Then connect this persistence to the existing mission/audit architecture before adding external integrations.
