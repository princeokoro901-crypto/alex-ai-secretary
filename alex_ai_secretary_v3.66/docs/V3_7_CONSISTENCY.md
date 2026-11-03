# Alex Core v3.7 — Audit/Orchestration Consistency

v3.7 adds a read-only consistency report between durable orchestration state and audit lifecycle events. It detects orchestration records with no completion audit, duplicate completion events, and audit lifecycle events that reference no persisted orchestration.

The report is diagnostic only: it does not mutate either store, repair records, or grant authority. `GET /api/v1/consistency` exposes the current report.

Known limitation: the current core uses separate persistence paths for orchestration and audit. A future transactional boundary can make cross-store lifecycle writes atomic.
