# Alex Core v3.2 — Persistent Orchestration State

## Goal
Make orchestration records and idempotency mappings survive a process restart without changing the existing mission/workforce authority model.

## Changes
- Added `OrchestrationStateStore` with optional JSON persistence.
- Configure persistence with `ALEX_ORCHESTRATION_STATE_PATH`.
- Orchestration results and idempotency-key mappings are loaded at startup.
- Writes use a temporary file plus atomic replacement and `fsync`.
- Persistence is optional; without the environment variable Alex keeps the prior in-memory behavior.
- `GET /health` reports whether orchestration persistence is configured.

## Safety
Persistence stores orchestration state only. It does not grant permissions, bypass runtime checks, or replace mission verification.

## Remaining
- Move durable orchestration and audit state to a transactional database for multi-process deployments.
- Add state-version migrations and corruption recovery.
- Add authenticated actor attribution to orchestration events when the auth boundary is integrated.
