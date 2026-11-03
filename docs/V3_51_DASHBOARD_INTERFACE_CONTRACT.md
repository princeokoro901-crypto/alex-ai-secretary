# v3.51 — Dashboard interface contract checks

## Purpose

This release adds static contract checks around the existing dashboard so common markup/JavaScript mismatches are caught in CI or local tests. It does not claim a real browser session or a live-server UI test.

## Checks added

- HTML IDs are unique.
- Every static `byId("...")` reference in dashboard JavaScript resolves to an element in the HTML.
- Inputs, textareas, and selects have a label or accessible name.
- Every button declares its `type` explicitly.
- Dashboard script and stylesheet are same-origin.
- Dashboard JavaScript avoids `innerHTML` and persistent `localStorage`; API-key storage remains session-scoped.
- Core mission, agent-assignment, recovery, and orchestration controls remain present.

## Validation

Run `python -m pytest -q`. These checks run without external services or browser networking.

## Limits and next step

Static checks cannot prove that event handlers work in a browser, that the responsive layout looks correct, or that keyboard/screen-reader interaction is usable. A real browser-to-live-server E2E run is still required in an environment where Chromium is permitted to reach loopback. No external integrations are enabled by this milestone.
