# v3.4 — Transactional Persistence Boundary

Alex now supports two interchangeable orchestration state backends:

- JSON: lightweight/local operation, with v3.3 migration and corruption quarantine.
- SQLite: transactional local persistence for safer multi-process development and a clearer path toward PostgreSQL.

Configuration:

- `ALEX_ORCHESTRATION_STATE_BACKEND=json|sqlite` (defaults to `json`)
- `ALEX_ORCHESTRATION_STATE_PATH=/path/to/state.json` or `/path/to/state.db`

The SQLite store persists orchestration results and idempotency mappings in separate tables and commits updates transactionally. It does not add authority, external access, credentials, or production deployment behavior.

Next: connect persistent orchestration records to the authenticated audit chain, then add key rotation/rate limiting before external integrations.
