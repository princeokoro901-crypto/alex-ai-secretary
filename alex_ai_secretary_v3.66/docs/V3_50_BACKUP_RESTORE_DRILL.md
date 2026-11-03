# v3.50 — Disposable backup/restore drill

## Purpose
Validate that a mission SQLite database and the append-only, hash-chained audit JSONL can be copied to disposable backup files and restored to new paths without losing the mission record or audit integrity. The test uses only temporary local files and a fixture mission; it does not access external services.

## Run
From the project root:

```bash
python scripts/backup_restore_drill.py
```

## Verified in this drill
- A fixture mission and audit event are written to temporary stores.
- SQLite's online backup API creates the database backup; `PRAGMA integrity_check` returns `ok`.
- The copied audit file has the same SHA-256 as the source and reloads with a valid hash chain.
- Restoring to separate paths preserves mission objective, budget, and assigned agent.
- A mission left in `running` is conservatively converted to `paused` on restore/startup, with manual review required and no results executed.
- A second reopen leaves the mission paused and does not duplicate the recovery checkpoint.

## Important limits
This is a controlled developer drill, not a production disaster-recovery certification. It does not test off-device backup, encryption/key recovery, access controls on backup media, backup scheduling, retention, restore-time objectives, concurrent writes during operational cutover, or reconciling audit events across independently restored stores. Keep a consistent recovery point for mission state and audit history. Never restore test fixtures over a production store. A restored interrupted mission must remain paused pending an authorized human decision.
