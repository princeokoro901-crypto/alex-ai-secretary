# Alex Core — development roadmap (updated through v3.66)

This roadmap reflects the code actually present in this release. Items below are not represented as completed features.

## v3.54 — Connection and permission error-state checks

Added `scripts/browser_connection_error_states_smoke.py` for mocked Chromium checks of service 503 handling, protected endpoint 403 visibility, administrator-readiness denial messaging, and API-key clearing from the field/session storage/request headers. This test complements, but does not replace, the v3.53 responsive live-server browser test.

## Immediate next step: direct-network and role-based browser validation (still outstanding)

1. Run the existing v3.53 Playwright dashboard test in a normal desktop environment without the API route bridge to validate direct browser networking, CORS, and normal asset loading. Then cover connect/clear-key flows and protected API error states.
2. Exercise administrator, operator, and reader roles through the UI against a local test server. Confirm that a non-admin cannot see administrator readiness data and that no dashboard control performs a write.
3. Continue the accessibility pass for keyboard navigation, focus visibility, labels, and screen-reader status announcements.

## Next functional slice: mission workspace (partial UI now exists)

1. Add a UI for creating and viewing missions using the existing API contracts.
2. Add an orchestration run form with clear objective, budget, and verification controls, then show run status/results returned by the API.
3. Keep consequential external actions separate: show exact action previews and approval status, require a different administrator actor where the current approval model requires it, and do not offer one-click bypasses of the single-use permit gateway.

## Integrations and operational work still required

- **Google/Gmail:** the code contains a gated Gmail sender and fake-transport tests, but no live Google credentials or live provider test was available for this release. Validate OAuth consent/scopes and delivery in a protected test deployment with a dedicated low-risk account before any production use.
- **Microsoft 365, WhatsApp, voice, phone calls, and remote office control:** these are not implemented by this codebase yet. They require separately designed official-provider integrations, permission and privacy controls, and real-device testing.
- **Language support:** broad Nigerian and world-language voice interaction is not established by the current backend. It needs speech recognition, text-to-speech, language routing, and tests for supported languages.
- **Production readiness:** rate limiting is process-local; multi-worker safety, secret management, TLS/reverse-proxy configuration, backups/restore, key rotation, deployment monitoring, and a security review remain outstanding.
- **Product scope:** Scam Shield and Opportunity Radar are future products, not modules included in this archive.

## Release discipline

For each subsequent milestone, keep the current tests and documentation, add focused tests for new behavior, rerun the full suite, record exactly which checks ran, and produce a single archive of the complete source tree. Never label a simulated provider test as a live integration test.

## v3.34 mission listing milestone

The dashboard now lists missions in the current in-memory MissionService store using the read-only `GET /api/v1/missions` endpoint. Listing requires `missions:read`; creating still requires `missions:write`. Mission cards display objective, ID, status, and budget and provide an Inspect button that populates the existing detail lookup. Creating a mission refreshes the list. This list is limited to missions present in the current runtime; it is not a durable cross-restart mission database.

Next: validate list/create/inspect roles in a browser, then design a controlled orchestration-run interface that visibly reports state and cannot bypass approvals or execution permits.


## v3.35 agent inventory groundwork

Added a least-privilege read-only agent inventory endpoint (`GET /api/v1/workforce/agents`) so the next UI slice can show registered agents before implementing assignment. Automated tests verify inventory availability and that reader-role access does not permit mission assignment. Agent assignment UI, orchestration run UI, and browser-level testing remain outstanding.


## v3.36 mission assignment UI

The dashboard now loads registered agents from `GET /api/v1/workforce/agents` and lets a connected user assign one to the currently inspected mission using the existing `missions:write` endpoint. The UI does not start missions or execute tasks. Automated static/API tests cover the interface contract; full real-browser testing is still outstanding. Next: validate role-specific behavior in a browser and then design a controlled orchestration run panel with clear status/result display.


## v3.37 controlled orchestration workspace

Added an explicit orchestration submission form to the dashboard. It requires checkbox confirmation and a second browser confirmation, validates the required tools/tool/budget/input JSON, sends to the existing authenticated orchestration endpoint, and renders returned results with `textContent`. The form is explicitly described as a simulated internal runtime path, not a live web/email/provider integration.

Next: exercise the complete form in a real browser with role-scoped API keys; test keyboard/screen-reader behavior and mobile layout; then build orchestration history/recovery views and separately design real tool execution behind the existing approval-bound execution gateway.


## v3.38 orchestration history and recovery

Added a bounded, read-only orchestration collection endpoint and dashboard history list with per-run inspection. Automatic retries are intentionally excluded: retrying uncertain or consequential work requires an explicit future recovery design with idempotency and execution-state checks. Next: browser automation for history loading, permission failures, mobile layout, and inspect actions; then design a safe recovery workflow backed by persisted request metadata and state-aware retry eligibility.


## v3.39 persistent task recovery

Added a durable pre-runtime orchestration intent record and idempotency mapping, startup conversion of leftover `running` records to `uncertain`, and an `orchestration_interrupted` audit event. Runtime exceptions after runtime entry are also represented as uncertain rather than retried. Recovery metadata omits raw input values and retains only input key names plus a SHA-256 digest. The dashboard reuses an idempotency key for unchanged form submissions to reduce duplicate task starts after a lost response.

Next: add an operator-facing recovery detail summary and safe, explicit disposition records (for example, acknowledged/no-retry) without automatically replaying work; test API authorization and real-browser behavior; then plan mission-state persistence separately. Validate that a configured durable store is backed up and protected before using it for sensitive workloads.


## v3.40 recovery review and acknowledgement

Added a separate `orchestration:review` permission and a one-time recovery-review API for uncertain runs. An authorized operator can record either `acknowledged_no_retry` or `escalated_for_manual_investigation`; the record includes reviewer identity and timestamp, and an audit event is emitted. The task remains uncertain and is never retried by this action. Existing reviews are immutable.

Next: add end-to-end browser automation for these controls and authorization errors; review persistence/audit failure behavior under simulated disk failures; design a mission-state store separately; then continue only after the tests show that a review cannot cause execution or bypass approvals.


## v3.43 mission recovery API and dashboard

Added `GET /api/v1/missions/recovery-queue` and `POST /api/v1/missions/{mission_id}/recovery-decision`, protected by `missions:read` and the dedicated `missions:recover` permission respectively. Operator/admin roles can make decisions; readers cannot. The dashboard displays restart-recovered missions and requires a reason plus confirmation before recording Keep Paused or Stop. Decisions are attributed to the authenticated actor and appended to the audit log. They never resume or retry work.

Next: consider transactional coupling/outbox for mission state and audit persistence, add browser-to-live-server E2E testing in an environment where browser networking is permitted, and preserve the no-auto-resume invariant.


## v3.44 recovery audit reliability
- Recovery decisions have stable IDs and persisted `audit_status` values.
- Audit delivery can be retried via the authorized recovery-audit endpoint without repeating the human decision.
- AuditLog supports idempotent event IDs and rejects conflicting reuse.
- Validation should cover API failure/retry behavior and restart persistence.

## v3.45 pending recovery audit queue
- Added operator/admin-only `GET /api/v1/missions/recovery-audit/pending`; reader-role keys are denied.
- Added a dashboard panel listing pending audit delivery and a retry control that uses the existing idempotent retry endpoint.
- The panel explicitly reports that audit retry does not repeat the decision or change mission execution state.
- Automated API tests verify pending queue visibility, least-privilege authorization, and queue clearing after successful retry.
- Chromium smoke test rendered the actual dashboard HTML/CSS/JavaScript and exercised the new panel against mocked API responses. This is not a live-server E2E test.

## Next: v3.46
- Run dashboard tests against a local live FastAPI server and real temporary SQLite/audit stores when a browser networking setup is available; distinguish those results from mocked UI tests.
- Add an operator-visible retry failure state test and verify the pending row remains available after simulated audit and mission-store write failures.
- Document tested backup/restore steps for the mission SQLite database and audit JSONL, including how to reconcile pending decisions after restoring either store.
- Preserve the invariant that audit retry never resumes or repeats mission execution.

## v3.47 follow-up

The local live-server smoke test is now available as `PYTHONPATH=. python scripts/live_server_smoke_test.py`. Next, add live-server API behavior tests for recovery queue authorization and audit-retry behavior using an isolated persistent test database, then test a backup/restore drill on disposable copies of the mission database and audit log. Keep all external connectors disabled in those tests.


## v3.48 live recovery API validation
- Added `scripts/live_recovery_api_test.py` to exercise the actual FastAPI server over loopback HTTP with disposable SQLite/audit storage and temporary role-scoped API keys.
- Validates restart recovery queue visibility, reader authorization denials, operator keep-paused decision, queue clearing, audit status, and duplicate-decision rejection.
- Next: run a disposable backup/restore drill for SQLite and JSONL, then add a fault-injection seam for live-process audit-write failures without adding any production-exposed failure switch.

## v3.49 live mission workspace API validation
- Added `scripts/live_mission_workspace_api_test.py`; it launches the actual FastAPI app on loopback and uses temporary SQLite/audit stores.
- Verified agent inventory, mission create/list/inspect, agent assignment, draft-state preservation, and negative-budget rejection over HTTP.
- Confirmed that agent assignment does not start the mission or create results.
- Attempted real Chromium-to-live-server testing, but this sandbox blocks browser navigation with `ERR_BLOCKED_BY_ADMINISTRATOR`. This is an environment limitation; no browser E2E pass is claimed.

## v3.50 backup/restore validation
- Added `scripts/backup_restore_drill.py` and `docs/V3_50_BACKUP_RESTORE_DRILL.md`.
- Verified SQLite online backup integrity, audit JSONL SHA-256 equality and hash-chain reload, restored mission data, and safe conversion of a restored `running` mission to `paused`.
- Verified a second reopen does not duplicate the recovery checkpoint. All data used was disposable and local.

## Next: v3.51
1. Run browser-to-live-server tests in an environment where Chromium is permitted to reach loopback, covering mission create/inspect/assign at the UI layer.
2. Add role-scoped UI tests and keyboard/accessibility checks.
3. Design a fault-injection test for audit persistence failure without exposing a production failure switch.
4. Production backup operations still need protected off-device storage, encryption/key recovery, access control, retention, restore objectives, and operational drills. Keep provider integrations disabled until the internal workflow passes.


## v3.51 dashboard interface contract checks
- Added `tests/test_dashboard_contract.py` for DOM ID uniqueness, static JS-to-HTML element references, explicit button types, accessible names for controls, same-origin assets, session-only key storage, and avoidance of unsafe `innerHTML` rendering.
- These are static/contract checks, not browser E2E. Chromium-to-loopback navigation remains blocked in this environment; test in a browser-enabled environment next.
- Next: run the full dashboard against a live server in an environment with browser networking, test role-specific UI states, keyboard navigation, mobile viewport behavior, and screen-reader announcements.


## v3.52 browser test bridge

A repeatable Chromium test now exercises the real dashboard against the live local FastAPI backend. In sandbox environments where Chromium cannot directly navigate to loopback, Playwright's route handler bridges API requests to the real server. This is not direct browser networking/CORS or production-deployment validation. Run `python scripts/browser_live_mission_workspace_test.py` with Playwright and Chromium installed. Next: run the test in a normal desktop environment without the bridge, then perform responsive viewport and keyboard/accessibility checks.


## v3.53 — Responsive browser and keyboard-focus checks

Extended `scripts/browser_live_mission_workspace_test.py` to run the live mission workflow at 320px, 375px, 768px, and 1280px viewport widths. Each size checks for horizontal page overflow and confirms the mission submission control remains visible. Added a keyboard-focus assertion for the connection form submit button. These are browser checks with a Playwright API bridge to the real local server; they do not validate direct browser networking, production hosting, or real provider integrations.


## v3.55 — Live role-based access matrix

Added an explicit `missions:read` permission entry for `GET /api/v1/missions/recovery-queue`, which is a read-only queue. The operational pending-audit queue and decision routes retain `missions:recover`. Added `scripts/live_role_access_matrix_test.py` for real local HTTP requests using isolated stores and test-only role keys. The test covers missing/invalid credentials, read-only routes, reader write denial, operator draft creation, operator denial from admin-only routes, and admin access to admin views. No mission is started.

## Next: v3.57 priorities

1. Continue role-aware tests for every protected API route and explicitly classify each route rather than depending on generic regex matches.
2. Add a systematic deny-by-default route inventory check so newly added protected endpoints cannot accidentally inherit an overly broad permission.
3. Run the browser without the Playwright API bridge in a normal desktop environment; direct networking/CORS/TLS/reverse-proxy behavior remains unverified here.
4. Continue deployment hardening: production secrets management, shared rate limiting, protected off-device backups, restore objectives, and operator runbooks.
5. Keep real provider integrations disabled until explicit credentials, consent, scoped permissions, approval gates, and safe integration tests are configured.


## v3.57 — Explicit API route permission coverage
- Classified the Gmail uncertain-send and reconciliation endpoints explicitly as administrator-only in `app/security.py`.
- Added `tests/test_route_permission_coverage.py` to catch newly registered API routes without explicit rules, conflicting overlaps, unknown permission names, and stale rules.
- This is a route-table contract test, not a full behavioral test of every endpoint or every role. Continue with systematic allow/deny matrices for sensitive routes and normal-environment direct browser networking.

## Next: v3.57
1. Expand live allow/deny tests for mission execution, approval decisions, execution permits, connector execution, and reconciliation mutations using safe invalid/no-op payloads where possible.
2. Ensure the tests never dispatch real external actions; keep all real providers disabled.
3. Test direct browser networking in a normal desktop environment, then assess production TLS, proxy headers, secret management, shared rate limiting, and off-device backup controls.


## v3.57 — Permission fallback regression checks
- Added focused tests for deny-by-default behavior on unclassified API routes and explicit administrator-only Gmail reconciliation rules.
- Updated live health/version expectations to v3.57.
- Next: broaden runtime role matrix coverage for consequential actions and test direct browser networking without the test bridge.


## v3.58 — Sensitive-action role matrix
- Extended local live-server role tests for approval decisions, Gmail reconciliation mutations, and connector execution denials.
- Confirmed reader/operator requests to approval decisions and Gmail reconciliation receive HTTP 403; reader connector execution also receives HTTP 403.
- Full automated suite: 274 passed. Live role matrix passed. No real providers or external actions were invoked.

## Next: v3.60
1. Build a route-by-role coverage matrix from the registered API route inventory, explicitly distinguishing read, draft/write, execute, approve, and admin-only operations.
2. Add safe positive-path checks only for actions whose effects can be isolated in disposable stores and disabled connectors.
3. Test direct browser-to-server networking in a normal desktop environment without the Playwright bridge.
4. Continue production deployment hardening and protected backup/restore operations.


## v3.60 — Registered route-by-role permission matrix
- Added a policy-contract test that checks every registered API method against the effective permission and each built-in role grant. It verifies that readers lack execution/approval/admin-only grants, operators lack approval-decision and `admin:all`, and unknown API routes retain the admin-only fallback.
- Validation run: full pytest suite, live local role-access matrix, live local server smoke test, and disposable backup/restore drill. Exact results are recorded in `TEST_RESULTS.txt`.

## Next: v3.60
1. Add safe runtime denial tests for mission start, agent execution, execution-permit creation, and execution reconciliation, using isolated stores and no external connector dispatch.
2. Add explicit audit assertions proving denied requests do not mutate approval, execution, or reconciliation state.
3. In a normal desktop/browser environment, validate direct browser-to-server networking without the Playwright route bridge; this sandbox has not established that capability.
4. Continue production hardening: shared rate limiting, secret management/key rotation, TLS/proxy configuration, and protected off-device backups.

## v3.60 — Runtime denial and no-mutation checks
- Added a runtime HTTP regression test for mission start, agent execution, execution-permit issuance, and execution reconciliation denial by role.
- The test asserts 403 responses and verifies mission state, approval/permit inventory, and audit-event count remain unchanged after the denied requests.
- Validation is local and in-memory; it does not prove production networking or external-provider behavior.

## Next: v3.62
1. Add an isolated-store test proving successful approval and permit state transitions are persisted correctly, while a denied actor cannot consume or replace a permit.
2. Add persistent execution-journal reconciliation tests covering invalid outcomes, duplicate/conflicting decisions, and audit failure boundaries.
3. Keep direct browser networking, accessibility, production deployment hardening, and off-device backup validation explicitly outstanding until run in a suitable environment.


## v3.62 — Approval, permit, and reconciliation lifecycle tests
- Added persistence/restart coverage for approval and execution-permit transitions, plus checks that role denial, action mismatch, duplicate issuance, and duplicate consumption leave permit state unchanged.
- Added execution-journal checks for invalid outcomes and conflicting duplicate reconciliation decisions.
- Characterized an unresolved cross-store boundary: the reconciliation row is committed to SQLite before audit append. If audit persistence fails, the API returns 503 but the row remains. This is surfaced in tests and must be addressed before treating this path as production-ready.

## Next: v3.62
1. Design a recoverable reconciliation workflow that avoids ambiguous partial commits across SQLite and the append-only audit log (for example, durable pending state plus idempotent completion/recovery).
2. Add restart/recovery tests for audit-write failure and safe operator visibility; never automatically repeat external actions.
3. Keep browser networking, production hardening, accessibility, and off-device backups outstanding until validated in a suitable environment.

## v3.62 — Durable reconciliation audit recovery
- Added a durable pending/recorded audit status and stable event ID to execution reconciliation records, with an in-place migration for existing SQLite journals.
- Added an administrator-only pending audit queue and audit-only retry endpoint. Both standard and Gmail-specific reconciliation use idempotent audit delivery.
- Added failure-injection tests for audit append failure and for a successful audit append followed by failed journal status update. Retry does not repeat the decision or action.
- Next: run the full browser workflow and role matrix; test direct browser networking without the route bridge in a normal desktop environment; continue production hardening and off-device backup validation. Real Gmail credentials and live provider delivery remain unverified.

## v3.63 — First practical local workspace workflow
- Added a local Markdown report-drafting endpoint and dashboard form/preview/download.
- The workflow is deterministic, preserves source lines, reports a SHA-256 digest, and explicitly discloses that it did not use an AI model or fact-check the notes.
- API access requires `missions:write`; input sizes are bounded and output integrity checks are returned.
- Next: browser-test this report workflow with the local server; then add a provider-neutral AI task-planning interface with explicit model configuration, timeouts, error handling, and source-grounded output verification. Do not claim the AI workflow works until a provider is configured and tested.
- Continue direct browser-to-server, deployment hardening, backup, and live integration validation in a suitable environment.


## v3.64 — Configured AI report drafting

Added a server-side OpenAI-compatible chat-completions adapter and opt-in AI report mode. Provider calls require explicit server-side URL, key, and model configuration; HTTPS is required except an explicitly enabled loopback-only HTTP development endpoint. The dashboard defaults to the local template and requires a user checkbox before notes are sent to an AI provider. AI output is size-limited, labeled for review, and never represented as fact-checked. All provider behavior in tests is mocked; no live provider call has been performed.

## Next: v3.65 priorities

1. Add a non-secret provider configuration/readiness view that clearly distinguishes configured from live-tested status.
2. Test provider timeout, HTTP error, malformed response, and oversize response behavior through the API.
3. Add a user-visible data-handling notice that identifies the configured provider before notes are sent.
4. Validate one real provider using an explicitly configured test account/key, only after the user/operator configures it; never include credentials in source or archive.
5. Keep direct browser-to-server networking and production deployment validation as separate tasks.


## v3.65 — AI provider configuration status
- Added `GET /api/v1/system/ai-provider-status`, protected by `system:read`.
- Status response exposes only a safe state, a bounded model label when configured, and whether a live connection was tested (always false for this configuration-only endpoint). It does not reveal API credentials or provider URL and does not contact the provider.
- Dashboard displays configuration state and disables AI mode when settings are missing/invalid or status cannot be retrieved. Local-template mode remains available.
- Next: add an explicit admin-triggered connection test with strict timeout, no secret/provider-body leakage, and tests for authentication errors, timeout, redirects, malformed responses, and provider availability. Only then configure and test one real provider.


## v3.66 — Explicit AI provider connection test

Added an administrator-only live probe endpoint and dashboard control. It sends a short acknowledgement prompt (not user report notes), uses an 8-second timeout, and returns only safe status metadata. Tests use mocked transport. No real provider credentials were available, so live connectivity remains unverified.

## Next priorities after v3.66

1. Configure one provider in a protected local test environment and run the explicit connection test; do not paste API keys into chat or commit them to source.
2. Verify successful and failed live responses with a low-risk test account, then run one non-sensitive AI report end to end.
3. Add request-level abuse protection for the provider-test endpoint and consider a cooldown to limit cost/abuse.
4. Validate direct browser-to-server networking, CORS, and deployment configuration.
5. Keep provider credentials in deployment secrets and review provider retention/privacy terms before sending user notes.
