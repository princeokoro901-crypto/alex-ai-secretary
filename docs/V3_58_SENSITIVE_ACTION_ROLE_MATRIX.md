# v3.58 — Sensitive-action role matrix

## Change

Extended `scripts/live_role_access_matrix_test.py` with local HTTP authorization checks for approval-decision mutations, Gmail uncertain-send reconciliation mutations, and connector execution. Reader and operator requests to approval decisions and Gmail reconciliation must return HTTP 403; reader connector-execution requests must also return HTTP 403. The tests use disposable identifiers and empty payloads so denied requests are rejected by authentication/authorization middleware before endpoint logic. No external provider is enabled or contacted.

Updated the application version and active test expectations to 3.58. Existing permission policy and human-approval controls were preserved.

## Validation performed

- `python -m pytest -q`: 274 passed.
- `python scripts/live_role_access_matrix_test.py`: passed all checks, including the new sensitive-action denial cases.
- Live test uses a temporary local HTTP server, test-only API keys, and disposable local storage.

## Limits

This does not prove that every role/action combination is covered, nor does it test real connector execution. Direct browser-to-server networking, CORS, TLS/reverse proxy, production deployment, and real Gmail/WhatsApp/voice integrations remain unverified.
