# v3.52 — Live API mission workspace browser test

## Purpose

Exercise the actual dashboard UI in Chromium while using the real local FastAPI backend and disposable data stores.

## Run

Install project dependencies from `requirements.txt`, ensure Playwright and Chromium are installed, then run:

```bash
python scripts/browser_live_mission_workspace_test.py
```

If Chromium is not on PATH, set `ALEX_CHROMIUM_PATH` to its executable path.

## What is tested

- Dashboard HTML, CSS, and JavaScript render in Chromium.
- The UI health connection reaches the live local backend.
- A mission can be created through the UI and appears as `draft`.
- Agent inventory can be loaded and an agent assigned through the UI.
- Assignment leaves the mission in `draft` with no results; it does not start execution.
- Mission list refresh displays the created mission.
- No uncaught browser JavaScript exceptions occur.

## Network limitation

The test runner intercepts the browser's same-origin API requests and forwards them to a real loopback Uvicorn server. This workaround is for environments where Chromium's direct navigation/network access to loopback is blocked. It tests real browser interaction and backend API behavior together, but does not prove direct browser-to-server networking, CORS, reverse-proxy behavior, TLS, or production deployment. Run a direct-network browser test in a normal desktop/browser environment before release.

No external services, real email, WhatsApp, voice calls, or consequential connector actions are invoked.
