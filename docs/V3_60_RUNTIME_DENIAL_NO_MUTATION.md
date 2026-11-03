# v3.60 — Runtime denial and no-mutation checks

## Scope

This release adds an HTTP-level authorization regression test for four sensitive operations:

- Reader attempts to start a mission.
- Reader attempts to execute an agent task.
- Reader attempts to issue an execution permit.
- Operator attempts to reconcile an execution, which remains administrator-only.

Each request must receive HTTP 403 from the authorization middleware. The test then checks that the pre-existing mission status and assignment are unchanged, the approval/permit record inventory is unchanged, and the audit-event count did not increase. This gives behavioral coverage beyond inspecting the permission table alone.

## Validation boundaries

- Uses FastAPI `TestClient` and local in-memory application services.
- Uses a unique temporary mission identifier to avoid collisions with other test runs.
- No external provider credentials, connector dispatch, or real-world actions are used.
- Does not replace live browser-to-server testing, production CORS/TLS/proxy validation, multi-worker rate-limit testing, or a full persistent-store failure-injection exercise.

## Run

From the project root:

```bash
python -m pytest -q tests/test_sensitive_action_runtime_denials.py
python -m pytest -q
python scripts/live_role_access_matrix_test.py
python scripts/live_server_smoke_test.py
python scripts/backup_restore_drill.py
```
