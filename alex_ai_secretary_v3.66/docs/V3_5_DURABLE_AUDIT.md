# v3.5 Durable Audit Integration

Alex now connects orchestration completion and idempotent replay to an append-only audit log.

## Configuration
- `ALEX_AUDIT_LOG_PATH=/path/to/audit.jsonl` enables durable JSONL audit storage.
- Without the variable, audit remains in-process only.

## Events
- `orchestration_completed`
- `orchestration_replayed`

Events include an id, UTC timestamp, actor, resource type/id, and structured details.

## Boundary
This is observability and evidence capture. It does not grant authority, replace authentication, or create external integrations.
