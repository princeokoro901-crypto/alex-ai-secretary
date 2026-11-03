# v3.33 — Mission workspace (create and inspect)

## What changed

The same-origin dashboard now provides a minimal mission workspace. An authorized operator can submit a mission contract to `POST /api/v1/missions` and inspect a known mission through `GET /api/v1/missions/{mission_id}`. The form captures an objective, non-negative budget, and optional scope. Verification remains required by default. Mission responses are rendered as text in a `<pre>` element, not parsed as HTML.

This is deliberately a limited first slice. It does not list all missions, assign agents, start orchestration, approve actions, issue execution permits, or execute connectors. It uses the existing API authorization checks and same-origin bearer-key handling. Error messages are shown in the page status region.

## Validation and limits

The automated dashboard tests verify that the workspace controls and API paths are present, that response display uses `textContent`, and that approval/execution shortcuts are not exposed. The full Python test suite and local HTTP smoke test should be run for this release. Browser engines are not bundled with the development environment, so this release does not claim Playwright browser execution or visual/responsive testing. No live provider integration was tested.

## Next

Install a supported Playwright browser in a controlled development environment and exercise connect/clear-key, mission create/lookup, reader/operator/admin permissions, network failures, keyboard navigation, and responsive layouts. Then add mission listing and carefully controlled orchestration-run UI without bypassing approval-bound execution.
