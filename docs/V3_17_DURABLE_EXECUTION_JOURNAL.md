# v3.17 — Durable Execution Journal and Recovery

## Change
Added an optional SQLite execution journal for the controlled execution gateway. When configured, the gateway persists an execution row before calling a registered handler and records terminal success/failure afterward. SQLite uses WAL and `synchronous=FULL`. On startup, rows left in `running` are changed to `uncertain`; Alex does not automatically retry them because the external side effect may already have occurred.

## Configuration
Set `ALEX_EXECUTION_JOURNAL_PATH` to a writable local path, for example `./data/executions.sqlite3`. If unset, the gateway retains its prior in-memory result behavior and does not provide durable execution recovery. Protect the database file and its directory with appropriate operating-system permissions and backups.

## Limits
The journal cannot make an external service's side effect atomic with SQLite. An operation may complete remotely and the process may crash before recording success; this appears as `uncertain` after restart and requires connector-specific reconciliation. A permit is consumed before journaling begins, so a crash or journal failure in that small interval can burn a permit without a journal row; the handler is not called in the journal-failure case. Do not automatically retry uncertain actions. Connector idempotency keys and explicit reconciliation are future work.

## Validation
Tests cover durable terminal outcomes and recovery of interrupted `running` rows as `uncertain`. This is not a production deployment or external-connector test.
