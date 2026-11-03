# Alex Core v3.3 — Durable State Reliability

## Implemented
- Added an explicit orchestration state schema version (`2` for the persisted JSON format).
- Added migration handling from the v1 state format.
- Added corruption recovery: unreadable or structurally invalid state is quarantined as `<path>.corrupt` and Alex starts with an empty orchestration state rather than crashing.
- Kept atomic temporary-file replacement and fsync from v3.2.
- Added regression tests for migration and corruption recovery.

## Safety / architecture
Persistence remains an infrastructure layer. It does not grant authority, change agent selection rules, or bypass mission/runtime checks.

## Not yet implemented
- Multi-process transactional database storage.
- Persistent audit integration in this release package.
- Key rotation, rate limiting, and external tool credentials.
- Production external integrations.

## Next
Move durable orchestration and audit state toward a transactional store with explicit migrations, then connect authenticated actor identity to the durable audit chain.
