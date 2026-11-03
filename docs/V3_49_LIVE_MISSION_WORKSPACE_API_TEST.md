# v3.49 — Live mission workspace API test

## Purpose
Validate the mission workspace's real API behavior using a locally running FastAPI server, not mocked API responses.

## Run
From the project root:

```bash
python scripts/live_mission_workspace_api_test.py
```

## Checks
- Live agent inventory returns registered agents.
- Mission creation returns the new mission in `draft` status.
- Mission list contains the created mission.
- Mission detail endpoint returns the expected objective.
- Agent assignment persists the selected agent and leaves mission status as `draft` with no results.
- A negative mission budget is rejected with HTTP 400.

All stores are temporary and the server binds to loopback. No provider API, external connector, real email, or consequential action is invoked.

## Browser test limitation
A Chromium UI test was attempted during this release, but the sandbox browser rejected navigation to the loopback server with `ERR_BLOCKED_BY_ADMINISTRATOR`. Therefore this release does **not** claim browser-to-live-server end-to-end validation. Run that test in a browser-enabled environment in a later milestone.
