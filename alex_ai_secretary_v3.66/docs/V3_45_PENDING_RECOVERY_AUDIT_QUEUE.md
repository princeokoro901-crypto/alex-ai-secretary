# v3.45 — Pending Recovery Audit Queue

## Delivered

- Added `GET /api/v1/missions/recovery-audit/pending`, which lists persisted recovery decisions whose `audit_status` is not `recorded` (including older records where the field is absent and therefore defaults to pending).
- The endpoint returns a list of pending mission/decision summaries: mission ID and objective, mission status, decision ID and decision, actor ID, timestamp, and audit status. It does not expose the human-entered reason in this queue.
- Added an explicit endpoint permission mapping requiring `missions:recover`; operator and admin roles can view the queue, while readers cannot.
- Added a dashboard panel with a refresh action and a per-mission **Retry audit delivery** button. On success the panel refreshes and removes the delivered record from the pending list. On failure it reports that the decision remains saved and allows the operator to retry.
- The UI calls the existing idempotent `POST /api/v1/missions/{mission_id}/recovery-audit/retry` endpoint. It does not repeat the decision or change mission status.
- Updated the manual Playwright/Chromium smoke script to exercise the new panel using mocked API responses.

## Validation

See `TEST_RESULTS.txt`. The full automated suite covers pending queue visibility, reader denial, successful queue clearing, and existing idempotent retry behavior. The Chromium smoke test loads the actual dashboard HTML, CSS, and JavaScript and verifies the new pending-audit flow with mocked API responses.

## Limitations and next work

- The browser smoke test is mocked; it is not a browser-to-live-server end-to-end test.
- Mission SQLite and audit JSONL remain separate stores. A failed audit write leaves a durable pending decision, but operators must protect both stores and reconcile them after backup restore.
- No external email, WhatsApp, voice, phone, or live Google integration was tested.
- Next: test the dashboard against a local live server where supported, exercise audit retry failures in the browser, and document backup/restore and reconciliation procedures. Do not automatically resume or retry mission work.
