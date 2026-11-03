# v3.37 — Controlled Orchestration Workspace

## What changed

- Added a task form to the same-origin dashboard that calls `POST /api/v1/orchestrate`.
- Fields: objective, task type, execution tool, required tools, budget, optional existing mission ID, optional candidate-agent IDs, and optional JSON input object.
- The selected execution tool must also appear in the required-tools list. Budget must be a finite non-negative number. Optional input must parse to a JSON object.
- The request always asks for verification and generates a unique orchestration idempotency key.
- Submission requires a checkbox and a second browser confirmation dialog. The form displays API errors and renders the result as text, not HTML.
- On success, the mission list refreshes and the resulting mission is loaded for inspection when possible.

## Safety and scope

This endpoint changes internal mission/task state and is protected by the existing `orchestration:run` permission. The UI makes the state change explicit. The current `AgentRuntime.submit` implementation simulates tool work: it records the task, authorization checks, verification evidence, and a result status. It does **not** perform real web searches, send email, call WhatsApp, or invoke external providers from this form. A successful simulated task is not proof of real-world completion.

The orchestration form is not an approval UI and does not issue execution permits. Consequential connector actions must continue to use the separate exact-action approval and single-use execution-permit gateway. Existing missions supplied to orchestration must already be running and have the selected agent assigned; otherwise the API rejects the request.

## Validation

- Automated suite: recorded in `TEST_RESULTS.txt`.
- JavaScript syntax: `node --check app/static/dashboard.js`.
- Python bytecode compilation: `PYTHONPATH=. python -m compileall -q app`.
- Static/API tests assert confirmation controls, request route, validation guards, verification flag, and safe text rendering.
- No real-browser interaction or live-provider test is claimed for this release.

## Next work

1. Test the form in a real browser using least-privilege role-specific keys.
2. Improve accessible focus and validation announcements and inspect small-screen layout.
3. Add orchestration history and detail lookup, plus retry/reconciliation UX that never automatically retries uncertain external actions.
4. Implement real tool execution only behind reviewed permissions, action previews, human approvals, and single-use permits.
