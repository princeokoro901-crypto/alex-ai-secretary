# v3.57 — Permission fallback regression checks

## Change

Added two focused security regression tests to `tests/test_security.py`: an unclassified API path must resolve to `admin:all` for both read and write methods, and the three Gmail uncertain-send/reconciliation routes must remain explicitly administrator-only. These tests complement the route-table coverage contract introduced in v3.56. No runtime authorization policy was relaxed.

The app health/version identifier and active live-test expectations are updated to v3.57. Historical release notes remain unchanged.

## Validation

Run from the project root with `python -m pytest -q`. The plain `pytest -q` executable in the build environment failed collection because the project root was not on its import path; `python -m pytest -q` correctly includes the current project directory and is the documented invocation.

This release's local test suite uses test-only keys and local application code. It does not contact Gmail or other external providers. Live server/browser scripts are present but were not rerun for this release unless listed in the release validation record.

## Remaining

- Extend runtime allow/deny matrices for execution, approvals, permits, connector actions, and reconciliation mutations.
- Test direct browser networking without the Playwright API bridge in a normal desktop environment.
- Complete deployment security and real-provider integration testing only when suitable credentials, consent, and safe test accounts are available.
