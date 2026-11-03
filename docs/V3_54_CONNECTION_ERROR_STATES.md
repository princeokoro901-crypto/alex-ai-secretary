# v3.54 — Browser connection and permission error states

Added `scripts/browser_connection_error_states_smoke.py`, an optional Chromium regression test using mocked API responses. It checks: a service HTTP 503 is shown as a recoverable connection error and re-enables Connect; a protected endpoint HTTP 403 is displayed without breaking the dashboard; administrator-only readiness denial is explained; Clear key removes the field and sessionStorage value; subsequent requests omit the Authorization header; and no uncaught browser JavaScript errors occur.

Run with Playwright and Chromium installed: `python scripts/browser_connection_error_states_smoke.py` (set `ALEX_CHROMIUM_PATH` if needed).

**Scope:** these are mocked API responses, not live-server, direct-network/CORS, deployment, or external-provider tests. The v3.53 live-server responsive mission test remains in the project.
