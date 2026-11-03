# v3.41 — Durable Mission State & Conservative Restart Recovery

## What changed

- `MissionService` supports optional SQLite persistence through `ALEX_MISSION_STORE_PATH` (or an explicit `store_path` for tests).
- Mission contracts, status, agent assignments, checkpoints, spend, and results are stored after lifecycle mutations.
- Existing mission records load on service startup.
- A mission left in `running` at process restart is changed to `paused` and receives an unverified `recovered_after_restart_manual_review_required` checkpoint.
- Completed/failed/stopped states are restored as recorded; no task or mission is automatically resumed.
- Duplicate mission IDs are rejected to avoid silently replacing existing state.

## Configuration

Set `ALEX_MISSION_STORE_PATH` to a writable SQLite file path in a protected persistent volume. If unset, behavior remains in-memory for backwards compatibility. Back up the database and protect its filesystem permissions.

## Safety and limits

A paused mission is not proof that its external work stopped. The recovery checkpoint requires human inspection. This persistence layer does not make workforce assignments, external side effects, or orchestration results transactionally atomic with mission state. It does not authorize connector execution or bypass approvals/one-time permits. No live external integrations are exercised by these changes.

## Validation

`tests/test_durable_missions.py` covers restart persistence, conservative recovery of a running mission, preservation of completed state, and duplicate-ID rejection. Run `PYTHONPATH=. pytest -q` from the project root.
