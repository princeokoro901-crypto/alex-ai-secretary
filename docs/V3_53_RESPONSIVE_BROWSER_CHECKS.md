# v3.53 — Responsive browser and keyboard-focus checks

## Purpose

Expand the live mission-workspace Chromium test to cover practical small-screen layout and keyboard navigation without claiming production browser networking.

## New browser checks

The existing Playwright test still starts the real local FastAPI app with temporary stores and performs mission create, agent assignment, and list refresh through the actual dashboard. It now also:

- checks 320px, 375px, 768px, and 1280px viewports;
- asserts the document does not overflow horizontally at those widths;
- asserts the mission form and submit control remain visible;
- uses keyboard Tab navigation to focus the connection form submit button;
- asserts the focused control has a visible CSS outline;
- retains the uncaught JavaScript error check.

## Run

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-browser-test.txt
python -m playwright install chromium
python scripts/browser_live_mission_workspace_test.py
```

If Chromium is installed outside Playwright's default browser path, set `ALEX_CHROMIUM_PATH` to its executable.

## Limits

API requests are bridged by Playwright's route handler to a real local Uvicorn server because sandbox browser networking can block direct loopback access. This validates UI interaction, responsive layout, focus styling, and local API behavior together, but does not validate direct browser networking/CORS, TLS/reverse-proxy behavior, production deployment, or external integrations. No external services or consequential actions are invoked.
