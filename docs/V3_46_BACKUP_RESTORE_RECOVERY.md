# v3.46 — Recovery-store backup and restore runbook

## Scope

This release documents a conservative operator procedure for the two stores involved in mission recovery decisions: the SQLite mission store (`ALEX_MISSION_STORE_PATH`) and append-only JSONL audit log (`ALEX_AUDIT_LOG_PATH`). They are separate stores and do not share a cross-store transaction. This is an operational runbook, not a claim that production restore has been exercised.

## Before backup

1. Put Alex into a maintenance window and stop all application workers that can write either store. Do not copy a live SQLite database with ordinary file-copy tools while writes may be occurring.
2. Confirm both environment variables resolve to the intended persistent files. If either is unset, the corresponding store may be in-memory and cannot be recovered from a file backup.
3. Copy both files to a restricted backup directory, preserving permissions and timestamps. Keep the pair together with a timestamp and deployment/configuration note; do not place secrets or database files in a public directory.
4. Record SHA-256 hashes for the two copied files and verify that the backup copies are readable. Protect backups as sensitive operational data.
5. Restart Alex only after the copies complete.

## Restore

1. Stop every Alex worker. Never restore one store while other workers are writing to either store.
2. Preserve the current files as a separate forensic copy before replacing anything.
3. Restore the mission SQLite file and audit JSONL file from the same backup point where possible, preserving restrictive permissions.
4. Start one instance in a controlled environment. Check application health, mission inventory, audit integrity, and `GET /api/v1/missions/recovery-audit/pending` with an authorized operator key.
5. If the pending queue is non-empty, inspect each decision and audit event ID. Use the authorized audit-retry endpoint only after confirming the restored audit log does not already contain a conflicting event. The retry operation is designed to be idempotent; do not repeat mission decisions or restart mission work as part of audit repair.
6. If the stores come from different points in time, an event conflicts, integrity verification fails, or a decision is missing from either store, keep the system in maintenance and escalate for manual reconciliation. Do not guess which store is authoritative and do not automatically resume missions.
7. Document the source backup timestamp, hashes, validation results, operator, and any manual reconciliation.

## Validation status and limitations

This runbook has not been validated against a production deployment or a real disaster-recovery exercise. The automated test suite exercises temporary SQLite and audit files and simulated audit-write failure/retry paths, not full backup restoration. Perform and record a restore drill in a protected non-production environment before relying on this procedure operationally.
