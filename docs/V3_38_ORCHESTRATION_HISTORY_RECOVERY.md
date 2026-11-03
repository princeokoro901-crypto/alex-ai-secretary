# v3.38 — Orchestration History & Recovery

## Delivered
- Added `GET /api/v1/orchestrations?limit=25` to list recent orchestration results without triggering work. The requested limit is clamped to 1–100.
- Applied the existing `orchestration:read` permission to the collection route.
- Added a dashboard history panel with status, orchestration ID, mission ID, selected agent, and an Inspect action that retrieves the full record through the existing detail endpoint.
- Inspection is read-only. Failed or uncertain tasks are not automatically retried.

## Scope and limitations
- The list reflects the currently configured orchestration state store. In-memory mode does not persist history across restarts.
- Existing result records do not retain all original request parameters, so this release does not offer one-click retry or reconstruction of a request.
- The new endpoint and dashboard behavior are covered by automated API/static tests. A real browser/mobile interaction test has not been performed.

## Next
Implement persisted, redacted request metadata and an explicit recovery eligibility model before adding retry controls. A retry must not replay uncertain external actions without reconciliation, approval, and idempotency checks.
