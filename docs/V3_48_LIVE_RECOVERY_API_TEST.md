# v3.48 — Live recovery API test

## Change
Added `scripts/live_recovery_api_test.py`, which seeds an interrupted mission in a disposable SQLite database, starts the real FastAPI application on loopback, and tests recovery operations over HTTP using temporary operator and reader API keys.

## Checks performed
- Operator can discover a mission paused after process restart.
- Reader is denied the pending-audit queue and recovery-decision write (HTTP 403).
- Operator can record `keep_paused`; the mission remains paused.
- The recovery queue clears after the decision and the pending audit queue is empty after successful audit persistence.
- A second decision is rejected (HTTP 409), preventing accidental replacement/repetition.
- All storage is temporary; real external integrations are disabled.

## Run
From the project root: `python scripts/live_recovery_api_test.py`

This is a live local HTTP test, not a production deployment test. It does not simulate an audit disk-write failure at the live-server process boundary and does not test Gmail, WhatsApp, voice, phone, or remote-office integrations.
