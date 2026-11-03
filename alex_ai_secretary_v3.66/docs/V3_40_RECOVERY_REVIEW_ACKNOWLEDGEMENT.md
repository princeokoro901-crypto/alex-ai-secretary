# Alex v3.40 — Recovery Review & Acknowledgement

## Purpose

Provide a durable, auditable human disposition for an orchestration whose outcome is already `uncertain`, without guessing what happened or retrying it.

## API

`POST /api/v1/orchestrations/{orchestration_id}/recovery-review`

Requires `orchestration:review`. The `admin` and `operator` roles have this permission; `reader` does not. The request body accepts exactly one of these `disposition` values:

- `acknowledged_no_retry` — the reviewer has inspected the record and explicitly chooses not to retry from this workflow.
- `escalated_for_manual_investigation` — the reviewer requests further investigation.

The stored review records disposition, UTC timestamp, actor ID, key ID, and role. A matching audit event is emitted. A review can be recorded only once, and only while the orchestration status is `uncertain`. Unknown runs return 404; invalid dispositions return 400; non-uncertain or already-reviewed runs return 409; unauthorized roles receive 403.

## Safety boundaries

This is a human disposition record, not a reconciliation of the external world. It does not alter orchestration status, prove that an external action occurred or did not occur, trigger a retry, or grant permission to execute connectors. Consequential connector operations continue to use the existing approval and execution-permit gateway. The UI asks for confirmation before submitting a review and renders server data using DOM text APIs.

The review is saved in the configured orchestration state store. If the audit append fails after the review is saved, the API reports that condition and advises the operator to inspect the run before taking further action; do not submit a second review. The record remains immutable.

## Validation scope

Automated tests cover both JSON and SQLite orchestration stores, immutability, actor attribution, audit events, non-uncertain rejection, API authorization, and dashboard contract text. An optional Chromium smoke script (`python scripts/manual_browser_smoke_recovery_review.py`) exercises the real dashboard markup, CSS, and JavaScript against mocked API responses; it requires Playwright and Chromium. Navigation from Chromium to a live local server was blocked by the execution environment policy, so this is not a browser-to-live-server end-to-end test. Disk-failure simulation for the review-store and audit-append boundaries is covered by unit tests; live external integration testing remains outstanding.
