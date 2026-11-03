# v3.42 — Mission Recovery Decisions

## Scope

Adds a service-layer, persisted recovery decision record for missions conservatively paused after an application restart. Supported decisions are `keep_paused` and `stop`. Each record requires an actor identifier and a non-empty reason, and is timestamped in UTC. Only one decision can be recorded per recovered mission.

## Safety behavior

- A recovered mission remains paused unless the decision is `stop`.
- `stop` moves the mission to the terminal `stopped` state.
- Neither decision resumes execution or repeats an action.
- `MissionService.start()` rejects restart-recovered missions so the normal start path cannot accidentally resume them.
- Recovery decisions persist in the existing mission SQLite payload; older records without the field load with an empty list.
- Persistence errors roll back the in-memory decision/status change.

## Authorization boundary

`record_recovery_decision` is a service-layer method and records actor attribution; it does not authenticate the actor. Any API/UI caller must enforce authorization before calling it. This milestone does not add a public recovery-decision endpoint or claim that an end-to-end authorized UI flow exists.

## Validation

Automated tests cover persisted `keep_paused` and `stop` decisions, required actor/reason, duplicate-decision rejection, and blocking restart-recovered missions from the ordinary start path. See `TEST_RESULTS.txt` for the run performed for this archive.

## Remaining work

Add an authenticated API endpoint wired to the existing endpoint-specific permission model, append an audit event, expose a human recovery queue in the dashboard, and test the full API-to-persistence path. Do not implement automatic resume; any future resume flow must be separately authorized and must reconcile possible external side effects first.
