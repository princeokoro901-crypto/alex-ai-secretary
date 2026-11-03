# v3.43 — Mission Recovery API and Dashboard

## Delivered

- Added `GET /api/v1/missions/recovery-queue` to list restart-recovered missions that remain paused and have no decision recorded.
- Added `POST /api/v1/missions/{mission_id}/recovery-decision` for `keep_paused` or `stop` decisions. The authenticated actor is taken from request middleware; caller-supplied actor identity is ignored.
- Added endpoint-specific `missions:recover` permission. Admin keys inherit it; operator keys have it; reader keys do not.
- Successful decisions append `mission_recovery_decision_recorded` to the existing audit log with actor/key attribution.
- Added dashboard recovery queue with reason input, confirmation, and Keep Paused / Stop Mission actions.
- Recovery decisions never resume or retry work.

## Failure semantics

The mission decision is persisted before the audit event is appended. If audit persistence fails, the API returns 503 with explicit wording that the decision was saved but audit persistence failed. Operators must inspect the mission and audit log before further action; the API does not claim rollback.

## Validation

See `TEST_RESULTS.txt`. Tests cover queue visibility, authenticated operator decision, reader denial, audit attribution, duplicate-decision conflict, and the non-resuming behavior. No live external integration is implied.

## Remaining

- Consider transactional coupling/outbox for mission decision + audit durability.
- Browser-to-live-server end-to-end testing still needs a runtime where browser/API networking is permitted.
- Never add automatic mission resumption; any future resume design must reconcile possible external side effects and enforce approval/permit rules.
