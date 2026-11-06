# v3.67 — Local reminders (workspace task)

Built on the verified v3.66 baseline (318/318 tests passing, confirmed by actually running
`PYTHONPATH=. pytest -q tests/` in a GitHub Codespace, not just read).

## What this adds
A third local workspace task alongside `draft_report` and `draft_ai_report`: `create_reminder`
(`app/reminder_workspace.py`). Same classification as `draft_report` — local-only, no external
calls, nothing sent anywhere — so it follows the same no-approval, audited-on-write pattern
rather than going through `ApprovalService`. Unlike a one-shot report draft, a reminder is
persisted so it can be listed later.

- `POST /api/v1/workspace/create-reminder` (`missions:write`) — `{"title": "...", "when": "..."}`.
  `when` is optional free text; `title` is required, max 160 chars; `when` max 160 chars.
- `GET /api/v1/workspace/reminders` (`missions:write`) — read-only list, soonest-due first,
  undated last.
- `ReminderStore`: SQLite, in-memory by default; set `ALEX_REMINDER_DB_PATH` for persistence
  across restarts (same `ALEX_<THING>_DB_PATH` convention as the rest of the app). The file is
  created with owner-only permissions where the OS supports it.
- `parse_when()`: a small, deterministic parser — trusted code, not the model — that recognises
  a fixed set of English phrases (today, tonight, tomorrow ± morning/afternoon/evening, a weekday
  name, "at H(:MM)am/pm", or combinations) and computes a concrete due time in UTC. Anything it
  doesn't recognise still creates the reminder with its original text; it just has no computed
  due time, and the response says so honestly rather than guessing.
- One new env var: `ALEX_TIMEZONE` (IANA name, e.g. `Africa/Lagos`; default UTC) — the single
  server-wide timezone used to resolve "when" phrases. No per-user timezone yet.

## Why no approval step
`draft_report` already establishes the precedent that local-only, no-external-effect tasks don't
need `ApprovalService` — only things that leave the system or change external state do. A
reminder fits that same risk profile. If a later feature lets a reminder *trigger* something
external (a notification, a connector call), that trigger should go through the approval and
execution-permit path like any other consequential action; this module only ever reads and
writes local rows.

## Tests
`tests/test_reminder_workspace.py`, 22 tests, written in the project's existing framework-free
style (plain functions, `tmp_path`/`monkeypatch` only, no `pytest.raises` — matching every other
test file that needs nothing beyond the stdlib). Verified by actually running them, twice:
against a standalone copy of the module, and again inside the full v3.67 tree. Two of the
trickier parser rules ("today stays today even once its time has passed," "a weekday is never
today, always a future day") were deliberately broken and confirmed to fail the suite, then
restored — not just asserted to exist.

## What I have not verified
- This file's own HTTP endpoints (`workspace_create_reminder`, `workspace_list_reminders`) were
  read and compiled cleanly, but not exercised end-to-end, since my sandbox has no network and
  cannot install `fastapi`. Please run `PYTHONPATH=. pytest -q tests/test_reminder_workspace.py`
  and the full suite in your Codespace and confirm the count.
- No test yet covers the two new HTTP routes directly through `TestClient` (the way
  `test_dashboard_contract.py` covers the dashboard) — worth adding once you confirm this runs.
- Nothing about a live AI provider changes in this release; that remains exactly as documented
  in v3.64–v3.66, still unverified, still blocked only on a real credential only you can supply.

## Changed files
- `app/main.py` — two new routes, one new store initialization, one new import line.
- `app/security.py` — two new permission entries (both `missions:write`).
- `app/reminder_workspace.py` — new.
- `tests/test_reminder_workspace.py` — new.

Nothing else touched. Diffed against the verified v3.66 archive to confirm.
