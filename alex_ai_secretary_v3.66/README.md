# Alex Core v3.66 — AI Workforce Foundation

Founding principle: **AI does the routine. Humans make the decisions.**

v3.15 binds approvals to the exact mission/action pair and adds single-use execution permits. It preserves v3.14 human approval and actor-attributed audit records, v3.13 endpoint-specific permissions, v3.12 process-local rate limiting, v3.11 API-key authentication, and the existing workforce/agent/orchestration/persistence/recovery architecture.

Architecture:

User -> Alex Orchestrator -> Workforce Manager -> Teams/Agents -> Tools -> Result Bus -> Verification -> Alex -> User

Read `docs/V3_15_APPROVAL_BOUND_EXECUTION_PERMITS.md` for behavior, configuration, validation, and remaining work.

## v3.16 Controlled Execution Gateway

The internal `ExecutionGateway` connects approved, exact-action-bound, single-use permits to handlers registered by trusted application code. No external connectors are registered by default. Handler failures consume the permit and are recorded rather than automatically retried; durable execution journaling and connector-specific recovery remain future work. See `docs/V3_16_CONTROLLED_EXECUTION_GATEWAY.md`.


## v3.17 Durable Execution Journal
The optional SQLite execution journal is enabled with `ALEX_EXECUTION_JOURNAL_PATH`. It persists execution attempts and marks rows left running after restart as `uncertain`; uncertain external outcomes are never automatically retried. See `docs/V3_17_DURABLE_EXECUTION_JOURNAL.md`.


## v3.18 Execution Reconciliation

Adds admin-only, append-only reconciliation records for uncertain executions, with required reasons and actor attribution. The original execution remains `uncertain`; this feature does not rerun actions or make retries safe. See `docs/V3_18_EXECUTION_RECONCILIATION.md` for endpoints, configuration, and deployment limitations.


## v3.19 Trusted Connector Framework

Adds a code-registered connector manifest/registry that validates connector inputs and role eligibility, then routes calls through the existing approval-bound execution gateway. Connector registration is intentionally not exposed as an API operation, and no real external connector is enabled by default. The read-only connector inventory is available at `GET /api/v1/connectors`. See `docs/V3_19_TRUSTED_CONNECTOR_FRAMEWORK.md`.


## v3.20 Sandbox Email Connector Prototype

Adds a simulation-only `sandbox.email` connector that validates a recipient, subject, and body, then records the message in a SQLite outbox. It never makes a network request and never sends email. Set `ALEX_SANDBOX_EMAIL_DB_PATH` to persist the simulated outbox across restarts; otherwise it uses in-memory SQLite. Use `POST /api/v1/connectors/sandbox.email/action-preview` to generate the exact approval string (including SHA-256 digests of subject and body), create an action proposal for that string, obtain an administrator approval, issue the single-use execution permit, then call `POST /api/v1/connectors/sandbox.email/execute`. The outbox listing endpoint is administrator-only. See `docs/V3_20_SANDBOX_EMAIL_CONNECTOR.md`.


## v3.21 Connector Reliability and Execution History

Adds an optional idempotency key to the simulation-only email connector, with approval-action binding, duplicate replay protection, conflict rejection, and a backward-compatible SQLite outbox migration. Adds admin-only bounded execution history at `GET /api/v1/executions` when `ALEX_EXECUTION_JOURNAL_PATH` is configured. See `docs/V3_21_CONNECTOR_RELIABILITY_AND_EXECUTION_HISTORY.md`.


## v3.22 Provider-neutral email integration boundary

Adds a provider adapter contract and trusted-code-only `EmailProviderRegistry`. Real delivery is fail-closed: a provider adapter must be registered in code, report an authorized account, be explicitly allowlisted with `ALEX_EMAIL_ALLOWED_PROVIDERS`, and the feature flag `ALEX_REAL_EMAIL_ENABLED=true` must be set. Provider actions bind the provider account and exact message content; an idempotency key is mandatory. Provider send errors and ambiguous/incomplete results are never automatically retried. The admin-only `GET /api/v1/email/providers` endpoint exposes a redacted readiness inventory. No real provider adapter is included or registered by default, so this release still cannot send external email. See `docs/V3_22_EMAIL_PROVIDER_BOUNDARY.md`.

## v3.23 — OAuth authorization and credential-safety foundation

Added internal primitives for one-time OAuth state, PKCE S256, trusted redirect-URI binding, encrypted credential storage, token-expiry status, and local credential revocation. These primitives are intentionally not exposed as public callback routes and do not perform real provider OAuth or email delivery. See `docs/V3_23_OAUTH_AUTHORIZATION_FOUNDATION.md` for configuration requirements, limitations, and the next integration step.


## v3.24 — Google OAuth adapter foundation

Adds a disabled-by-default Google OAuth adapter that builds authorization URLs using the existing one-time state and PKCE services, exchanges codes through a fixed-endpoint server-side transport, verifies the Google user-info identity, and stores tokens in the encrypted vault. Tests use a fake transport; no live Google credentials or external requests are used. No HTTP callback routes or Gmail sender are registered, so this release still cannot send real email. See `docs/V3_24_GOOGLE_OAUTH_ADAPTER.md`.


## v3.25 — Session-bound Google OAuth callback

Adds a disabled-by-default server-side Google OAuth browser flow: administrator-authenticated start endpoint, short-lived HttpOnly/SameSite browser-binding cookie, hashed binding/state persistence, atomic callback consumption, fixed redirect, duplicate-query rejection, and generic landing page. OAuth credentials are now bound to the initiating actor in the encrypted vault; a different actor cannot retrieve, revoke, or overwrite the linked account. Existing ownerless vault rows are hidden and require reauthorization. This links an account only; it does not send email. No live Google credentials or external requests were used in tests. Configure all `ALEX_GOOGLE_OAUTH_*` values and set `ALEX_GOOGLE_OAUTH_ENABLED=true` only in a protected deployment after registering the exact callback URI. See `docs/V3_25_SESSION_BOUND_GOOGLE_OAUTH_CALLBACK.md`.


## v3.26 — Connected-account management and token lifecycle

Adds owner-scoped, redacted connected-account inventory at `GET /api/v1/email/google/accounts`, a token-refresh endpoint at `POST /api/v1/email/google/accounts/{account_id}/refresh`, and a disconnect endpoint at `POST /api/v1/email/google/accounts/{account_id}/disconnect`. These endpoints are administrator-only, use the initiating actor identity, and never return token values. The OAuth credential vault now stores a masked account label and coordinates refresh with a durable SQLite lease so simultaneous workers do not refresh the same account concurrently. Expired leases can be reclaimed after a process interruption; stale workers cannot overwrite newer credentials. Google token refresh preserves the existing refresh token if Google does not rotate it. Disconnect always disables local credentials, and reports provider-side revocation separately because a network failure cannot guarantee the external grant was revoked.

Google OAuth remains disabled unless explicitly configured. The tests use simulated transports; no live Google requests or credentials were used. This milestone still does not implement Gmail message delivery or register a real email sender with the execution gateway. Before production use, review API key/session deployment, SQLite permissions and backups, encryption-key rotation, proxy log redaction, and provider revocation behavior. See `docs/V3_26_CONNECTED_ACCOUNT_MANAGEMENT_AND_TOKEN_LIFECYCLE.md`.


## v3.27 — Approval-bound Gmail sender

Adds a concrete Gmail API sender that remains disabled and unregistered unless the operator enables `ALEX_GMAIL_SENDER_ENABLED=true`, the general real-email flag is enabled, `gmail` is allowlisted, a specific owner-scoped Google account is configured, and durable approval/audit/execution/send-ledger stores are set. The sender is registered as `gmail.email` in the existing connector registry and can execute only through the approval-bound gateway with an exact account/content/idempotency-key action. It constructs a MIME message safely, refreshes an expiring access token through the existing OAuth lifecycle, and uses a durable SQLite ledger to suppress duplicate successful sends and block retries of unresolved attempts. Ambiguous Gmail results are recorded as `uncertain` rather than `failed`, and Alex does not resend automatically. The preview endpoint is `POST /api/v1/connectors/gmail.email/action-preview`; previewing does not send. See `docs/V3_27_GMAIL_SENDING_INTEGRATION.md` for required configuration, workflow, and limitations.

This milestone is tested with fake transports only. No live Google credentials or external email sends were used; real sending remains disabled unless all explicit gates are configured.


## v3.28 — Gmail uncertain-send investigation queue

Adds an administrator-only, account-scoped endpoint to inspect unresolved Gmail send attempts without exposing message content, recipients, idempotency keys, payload hashes, or OAuth tokens. It is metadata-only: it does not prove delivery, reconcile executions, or permit retries. See `docs/V3_28_GMAIL_UNCERTAIN_SEND_INVESTIGATION.md`.


## v3.29 — Gmail and execution-journal reconciliation

Adds optional trusted execution context to the controlled gateway and passes the generated execution ID into the Gmail send ledger. Existing SQLite send ledgers migrate safely with a nullable `execution_id`; legacy attempts are deliberately marked unlinked rather than heuristically matched. The admin-only `GET /api/v1/connectors/gmail.email/reconciliation` cross-references unresolved attempts with execution journal records, and `POST /api/v1/connectors/gmail.email/uncertain-sends/{attempt_id}/reconcile` records an evidence-referenced, append-only admin decision. These operations do not change send/execution status, unblock idempotency keys, or retry email. See `docs/V3_29_GMAIL_EXECUTION_RECONCILIATION.md`.



## v3.30 — Operational readiness diagnostics

Adds administrator-only `GET /api/v1/system/readiness`, a read-only deployment checklist for API administrator-key configuration, persistent stores, audit integrity, OAuth storage, Gmail feature flags/provider allowlisting, and owner-scoped account authorization. It exposes only safe status metadata and never returns secrets or configured paths. The endpoint does not enable delivery or prove live-provider behavior. See `docs/V3_30_OPERATIONAL_READINESS.md`.


## v3.31 — First-run setup preflight and end-to-end Gmail workflow tests

Adds a read-only local setup checker, runnable with `python -m app.setup_check` (or `python scripts/alex_setup_check.py` from the project root). It validates boolean feature flags, API-key configuration, OAuth encryption-key format and redirect-URI shape, Gmail feature-gate relationships, required persistent-store settings, and writable storage locations without creating files, printing secret values, connecting to providers, or enabling integrations. With Gmail disabled it reports sandbox-only mode and explicitly states that no external email can be sent by the checker.

Adds API-level integration tests that traverse Gmail action preview, mission action proposal, independent administrator approval, single-use permit issuance, and execution through the existing gateway. Tests use a fake Google transport, verify successful journal/ledger updates, confirm a successful idempotency replay does not make a second provider call, and reject payload changes after approval. No live Google credentials or requests are used. See `docs/V3_31_FIRST_RUN_SETUP_AND_INTEGRATION_TESTING.md`.

## Local validation

Install `requirements.txt`, then run `PYTHONPATH=. pytest -q` from the project root. For a read-only first-run configuration report, run `python -m app.setup_check`; add `--json` for machine-readable output. Tests use fake provider transports and do not send external email.


## v3.32 — Read-only browser console

Adds a responsive same-origin dashboard at `/` for service health, registered connectors, pending approvals, and administrator-only redacted readiness diagnostics. API keys are held in browser `sessionStorage` for the current tab only and are sent only to same-origin API routes. The page is read-only: it cannot approve actions or execute connectors. The existing API authorization and human approval-bound execution workflow remains in force. See `docs/V3_32_READ_ONLY_WEB_CONSOLE.md` for security notes and limits.

Run locally with `uvicorn app.main:app --host 127.0.0.1 --port 8000`, then open `http://127.0.0.1:8000/`. No live Gmail integration is implied by the dashboard.

## v3.33 — Mission workspace

Adds a minimal mission creation and lookup workspace to the same-origin dashboard. It submits mission contracts to the existing API and displays results as text. It intentionally does not assign agents, start missions, approve actions, issue execution permits, or execute tools. See `docs/V3_33_MISSION_WORKSPACE.md` for scope, validation limits, and the next steps.


## v3.35 Registered-agent inventory

Adds read-only `GET /api/v1/workforce/agents`, protected by `workforce:read`, returning registered agent identifiers, names, roles, and capabilities for future mission-assignment UI work. The endpoint does not assign agents, start missions, or execute tasks. Assignment remains a separate `missions:write` operation.


## v3.36 Mission agent assignment workspace

Adds a dashboard control to load registered agent definitions, inspect a mission, and assign a selected agent through the existing `POST /api/v1/missions/{mission_id}/agents` API. Assignment is distinct from starting a mission or executing tasks. The UI surfaces assignment errors from the API and refreshes mission inventory after success. Browser-level interaction remains unverified in this environment.


## v3.37 Controlled orchestration workspace

Adds a dashboard form for submitting one internal agent-runtime task through the existing authenticated `POST /api/v1/orchestrate` endpoint. The form requires explicit checkbox confirmation and a second browser confirmation, validates objective/task type/tool list/budget and optional JSON input, generates an idempotency key, and displays the returned orchestration result as text. It can optionally target an already-running mission or constrain candidate agent IDs.

**Important scope limit:** the current `AgentRuntime` records simulated task metadata and verification results; this UI does not perform a live web search, send an email, or invoke external providers. The orchestration form is a state-changing operation and should only be used with an API key authorized for `orchestration:run`. It does not replace the separate approval and single-use execution-permit path for consequential connector actions. Browser-level testing remains outstanding. See `docs/V3_37_CONTROLLED_ORCHESTRATION_WORKSPACE.md`.


## v3.38 orchestration history and recovery workspace

Adds a read-only `GET /api/v1/orchestrations?limit=25` collection endpoint (bounded to 1–100 items) and a dashboard history panel. Operators can inspect recent run results using the existing detail endpoint. Failed or uncertain runs are not automatically retried; the interface makes inspection and recovery decisions explicit. History access requires `orchestration:read`. It reflects the active orchestration state store and does not create a durable record if the configured store is in-memory. See `docs/V3_38_ORCHESTRATION_HISTORY_RECOVERY.md`.


## v3.39 persistent task recovery

When persistent orchestration storage is configured, Alex now writes an `execution_pending` orchestration record and the request idempotency mapping before entering the agent runtime. The record contains bounded request metadata (objective, task/tool details, input key names, and a SHA-256 digest of input JSON), not raw input values. A durable run still marked `running` on application startup is conservatively changed to `uncertain` and audited as `orchestration_interrupted`. Runtime exceptions after entering the runtime are also recorded as uncertain. Neither path retries work automatically, and replaying the same idempotency key returns the stored uncertain result instead of starting another task. This cannot guarantee recovery when persistent storage is not configured, and it does not make mission state durable. See `docs/V3_39_PERSISTENT_TASK_RECOVERY.md`.


## v3.40 recovery review and acknowledgement

Adds a one-time `POST /api/v1/orchestrations/{id}/recovery-review` operation for runs whose status is already `uncertain`. Authorized operators can record `acknowledged_no_retry` or `escalated_for_manual_investigation`. The review captures the actor ID, key ID, role, and UTC timestamp in the orchestration record and emits an `orchestration_recovery_reviewed` audit event. A review is immutable; a second review is rejected. This does not change task status, assert the real-world outcome, retry work, or bypass connector approvals/permits. The endpoint requires the distinct `orchestration:review` permission, granted to admin and operator roles but not reader roles. See `docs/V3_40_RECOVERY_REVIEW_ACKNOWLEDGEMENT.md`. An optional mocked-API Chromium smoke test is available at `scripts/manual_browser_smoke_recovery_review.py` when Playwright and Chromium are installed.

## v3.41 — Durable mission state and conservative restart recovery

`MissionService` can persist mission contracts, status, assignments, checkpoints, spend, and results in SQLite when `ALEX_MISSION_STORE_PATH` points to a writable file. Missions found `running` after restart are changed to `paused` and receive an unverified manual-review checkpoint; they are not resumed automatically. Unset configuration preserves in-memory behavior. See `docs/V3_41_DURABLE_MISSION_STATE.md` for limitations and safety notes.

## v3.42 — Mission recovery decisions

Adds persisted, actor-attributed recovery decisions (`keep_paused` or `stop`) for missions paused after restart. Decisions require a reason, are one-time, and do not resume work. The ordinary `start()` method now refuses to resume a restart-recovered mission. This release adds service-layer behavior only; authenticated API/UI wiring and audit-event integration remain future work. See `docs/V3_42_MISSION_RECOVERY_DECISIONS.md`.


## v3.43 — Mission Recovery API and Dashboard

Adds an authenticated mission recovery queue and decision endpoint, the dedicated `missions:recover` permission, audit attribution for recovery decisions, and a dashboard queue with explicit confirmation. `keep_paused` leaves the mission paused; `stop` moves it to stopped. Neither decision resumes or retries work. See `docs/V3_43_MISSION_RECOVERY_API_DASHBOARD.md`.


## v3.44 — Recovery audit reliability

Recovery decisions now carry a stable decision ID and durable audit-delivery state (`pending` or `recorded`). If the audit log cannot be written after the decision is saved, the API returns `503` and clearly directs the operator to retry audit delivery rather than repeat the decision. The authorized `POST /api/v1/missions/{mission_id}/recovery-audit/retry` endpoint retries the audit record without resuming the mission or creating a second decision. Audit events accept an optional stable event ID and reject conflicting reuse, making retries idempotent within the current audit log.

This improves recoverability but does not make the mission database and audit JSONL a single cross-store transaction. Operators should continue to monitor pending audit states and protect both stores with backups.


## v3.45 — Pending recovery audit queue

Adds an operator/admin-only `GET /api/v1/missions/recovery-audit/pending` endpoint and a dashboard panel for recovery decisions whose audit delivery is still pending. Each row shows the mission, decision, actor, and mission status, with a retry control that calls the existing idempotent audit-retry endpoint. Readers are denied access to this operational queue. Retrying only attempts audit delivery; it does not repeat the human decision, resume a mission, or rerun work.

Validation includes API authorization and queue-clearing tests plus a Chromium dashboard smoke test using mocked API responses. The browser test does not represent a live-server or external-provider integration test. See `docs/V3_45_PENDING_RECOVERY_AUDIT_QUEUE.md`.

## v3.46 — Recovery-store backup and restore runbook

Adds `docs/V3_46_BACKUP_RESTORE_RECOVERY.md`, a conservative operational procedure for backing up and restoring the SQLite mission store and JSONL audit log as a coordinated pair. It explicitly distinguishes documented guidance from a restore drill: production restore has not been performed, and operators must stop writers, preserve forensic copies, verify audit integrity, inspect pending recovery-audit records, and avoid automatically resuming mission work. Dashboard tests also assert that a failed audit retry leaves a clear operator-visible failure message and does not imply the decision was lost.


## v3.47 — Live-server smoke test

Updates the FastAPI application title and health endpoint to v3.47, and adds a repeatable local live-server smoke-test script. The script starts Uvicorn on a loopback-only ephemeral port, verifies the health JSON, dashboard HTML and JavaScript/CSS assets, and confirms the local-development readiness endpoint is reachable. It does not contact external providers or prove real Gmail/WhatsApp/voice integrations. See `docs/V3_47_LIVE_SERVER_SMOKE_TEST.md`.


## v3.48 validation
The release includes `scripts/live_recovery_api_test.py`, a loopback-only HTTP test for recovery queue authorization and decision behavior. See `docs/V3_48_LIVE_RECOVERY_API_TEST.md`.

## v3.48 — Live recovery API validation

Adds a loopback-only HTTP integration test for restart recovery and recovery-decision authorization. The test uses disposable SQLite/audit files and test-only operator/reader keys; no external providers are contacted. See `docs/V3_48_LIVE_RECOVERY_API_TEST.md`.

## v3.49 — Live mission workspace API validation

The FastAPI title and health endpoint now identify v3.49. Added `scripts/live_mission_workspace_api_test.py`, which starts the real local server with disposable persistent stores and validates mission creation, listing, inspection, agent inventory, agent assignment, and rejection of a negative budget. The test confirms assignment leaves the mission in `draft` with no results and does not start execution.

A real Chromium-to-live-server UI test was attempted in this sandbox but Chromium navigation was blocked by the environment (`ERR_BLOCKED_BY_ADMINISTRATOR`). That is recorded as an environment limitation, not a passing browser test. Browser interaction against a live server remains the next testing milestone. No external integrations were contacted.


## v3.50 — Disposable backup/restore drill

Added `scripts/backup_restore_drill.py` and `docs/V3_50_BACKUP_RESTORE_DRILL.md`. The drill uses SQLite's backup API and disposable files to validate database integrity, audit JSONL hash-chain integrity, mission state restoration, and conservative pause-on-restart behavior. It does not establish production backup readiness. The app title and health endpoint identify v3.50. Browser-to-live-server E2E remains blocked in this sandbox and is not claimed as passed.


## v3.51 — Dashboard interface contract checks

Added `tests/test_dashboard_contract.py` to validate the dashboard HTML/JavaScript contract without pretending to perform browser E2E. It checks unique HTML IDs, that every static `byId(...)` JavaScript reference exists in the HTML, explicit button types, accessible names for interactive controls, same-origin asset loading, session-only API-key storage, and DOM-safe rendering. The FastAPI title and health endpoint identify v3.51. These checks reduce common interface regressions but do not replace live browser interaction; browser-to-live-server E2E remains outstanding because Chromium navigation was blocked in this environment.


## v3.52 — Chromium browser test against the live local API

Adds `scripts/browser_live_mission_workspace_test.py`. It runs the real local FastAPI application with isolated temporary SQLite/audit stores, renders the actual dashboard HTML/CSS/JavaScript in Chromium, and bridges browser API calls through the Playwright test runner to the live local server. It checks the UI connection, mission creation, mission list refresh, agent inventory, assignment, draft-state safety, and uncaught browser errors. This bridge is a sandbox workaround: it tests real browser interaction and real backend behavior but does not test direct browser-to-loopback networking, CORS, reverse proxies, or production deployment. Requires Playwright and Chromium; set `ALEX_CHROMIUM_PATH` if Chromium is not on PATH. Run with `python scripts/browser_live_mission_workspace_test.py`. No external providers are contacted.


### Running the browser test (v3.52)

The browser test uses Chromium through Playwright and starts its own isolated local server. From a terminal in the project folder:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-browser-test.txt
python -m playwright install chromium
python scripts/browser_live_mission_workspace_test.py
```

On Linux, if the Chromium executable is already installed, set `ALEX_CHROMIUM_PATH` to its path (for example `/usr/bin/chromium`). The test uses disposable stores and does not require real provider credentials. See `docs/V3_52_LIVE_BROWSER_MISSION_WORKSPACE.md` for what the browser bridge does and does not prove.

## v3.54 — Responsive browser and keyboard checks

The live Chromium mission-workspace test now checks 320px, 375px, 768px, and 1280px layouts for horizontal overflow and a visible mission submit control. It also checks keyboard Tab navigation and visible focus styling. Run it with `python scripts/browser_live_mission_workspace_test.py` after installing `requirements-browser-test.txt` and Chromium. See `docs/V3_53_RESPONSIVE_BROWSER_CHECKS.md` for scope and limitations.


## v3.54 — Connection error-state regression checks

Added an optional mocked Chromium test for recoverable service errors, protected-endpoint permission errors, and API-key clearing. Run `python scripts/browser_connection_error_states_smoke.py` with Playwright and Chromium installed. This is a mocked UI test, not direct-network or provider validation. See `docs/V3_54_CONNECTION_ERROR_STATES.md`.


## v3.55 — Role-based access matrix

Made the read-only mission recovery queue permission explicit as `missions:read`, separate from the stronger operational pending-audit and recovery-decision permission. Added `scripts/live_role_access_matrix_test.py` to start a disposable local server and check admin/operator/reader allow/deny behavior over HTTP. It verifies missing and invalid key rejection, shared read-only inventory access, reader write denial, operator draft creation, operator restrictions on admin-only views, and administrator access to readiness/approval/provider inventories. The created mission remains a draft and is never started. Run `python scripts/live_role_access_matrix_test.py`. No external provider is contacted. See `docs/V3_55_ROLE_ACCESS_MATRIX.md`.


## v3.57 — Explicit API route permission coverage

Added three explicit `admin:all` rules for Gmail uncertain-send inventory, reconciliation inventory, and uncertain-send reconciliation. Those endpoints were already fail-closed through the fallback and also contain administrator checks; this change makes their security classification visible in the permission inventory. Added `tests/test_route_permission_coverage.py`, which checks that every registered `/api/v1/` route method has a matching explicit rule, overlapping rules do not disagree on permission, every permission is recognized, and no rule has become stale. Updated active version checks and the live health response to v3.57. Run `python -m pytest -q` and `python scripts/live_role_access_matrix_test.py`. These checks do not test production networking or external providers. See `docs/V3_56_ROUTE_PERMISSION_COVERAGE.md`.


## v3.57 — Permission fallback regression checks

Added regression tests proving that unknown API routes default to `admin:all` and the Gmail uncertain-send/reconciliation routes remain administrator-only. Updated active version checks and health response to v3.57. Run `python -m pytest -q`. See `docs/V3_57_PERMISSION_FAIL_CLOSED_REGRESSION.md`.


## v3.58 — Sensitive-action role matrix

Extended the live local HTTP role matrix to verify that readers and operators are denied approval-decision and Gmail reconciliation mutations, and that readers are denied connector execution. The tests use disposable identifiers and are rejected by authorization middleware before endpoint logic; no external action is invoked. Active app and test version references are updated to v3.58. Run `python -m pytest -q` and `python scripts/live_role_access_matrix_test.py`. See `docs/V3_58_SENSITIVE_ACTION_ROLE_MATRIX.md`.

## v3.60 — Registered route-by-role permission matrix

Added `tests/test_role_permission_matrix.py` to compare every registered `/api/v1/` route method against the effective permission and the built-in administrator/operator/reader grants. The tests assert that readers lack execution, approval-decision, and administrator-only grants; operators cannot decide approvals or use `admin:all`; and unclassified routes remain admin-only. This is a policy-contract test, not proof that every route has been behaviorally exercised. Run `python -m pytest -q`, `python scripts/live_role_access_matrix_test.py`, `python scripts/live_server_smoke_test.py`, and `python scripts/backup_restore_drill.py`. See `docs/V3_59_ROLE_PERMISSION_MATRIX.md`.

## v3.60 — Runtime denial and no-mutation checks

Added HTTP-level regression coverage for reader-denied mission start, agent execution, and execution-permit issuance, plus operator-denied execution reconciliation. The test asserts each request is rejected by authorization middleware before endpoint logic, and verifies mission state, approval/permit records, and audit-event count remain unchanged. Tests use local in-memory services and no external provider dispatch. See `docs/V3_60_RUNTIME_DENIAL_NO_MUTATION.md`.


## v3.62 — Approval, permit, and reconciliation lifecycle tests

Added isolated-store tests for approval and single-use execution-permit persistence across service restarts, action binding, unauthorized consumption, duplicate issuance, and duplicate consumption. Added execution-journal tests for invalid and conflicting reconciliation decisions. A deliberate audit-write-failure test documents a remaining cross-store consistency risk: the SQLite reconciliation row is committed before the separate audit append, so a failed audit returns HTTP 503 but leaves a reconciliation row that needs operator review. No external action is dispatched by these tests. See `docs/V3_61_LIFECYCLE_TESTS_AND_AUDIT_BOUNDARY.md`.

## v3.62 — Durable reconciliation audit recovery

Execution reconciliation rows now track durable audit delivery status and a stable event ID. If audit persistence fails, administrators can inspect the pending queue and retry audit delivery without repeating the reconciliation decision or an external action. Existing SQLite execution journals are upgraded in place. The recovery path is covered by failure-injection tests using temporary local stores and a fake audit sink. See `docs/V3_62_RECONCILIATION_AUDIT_RECOVERY.md` and `TEST_RESULTS.txt` for exact validation and limitations.

## v3.63 — First practical local workspace task

Adds a dashboard workflow that creates a reviewable Markdown report from user-supplied notes, verifies that the title and source lines are preserved, shows a preview, and downloads the result. It uses a deterministic local template only: no AI model, web research, or external actions are invoked. The API requires the existing `missions:write` permission and bounds input sizes. See `docs/V3_63_LOCAL_REPORT_WORKFLOW.md`.


## v3.64 — Configured AI report drafting

Adds an opt-in AI report endpoint using an OpenAI-compatible chat-completions API. The provider URL, API key, and model are configured server-side with `ALEX_LLM_BASE_URL`, `ALEX_LLM_API_KEY`, and `ALEX_LLM_MODEL`. HTTPS is required; HTTP is permitted only for loopback hosts when `ALEX_LLM_ALLOW_INSECURE_LOCAL=true` is explicitly set. The dashboard keeps local-template mode as the default and offers a separate checkbox to send notes to the configured AI provider. Provider credentials never enter browser code. AI drafts are bounded, labeled for human review, and never claim independent fact-checking. Tests use a fake local transport; no live provider was contacted or validated in this release. See `docs/V3_64_CONFIGURED_AI_REPORT_DRAFTING.md`.


## v3.65 — AI provider configuration status

Adds a protected, read-only provider-status endpoint and dashboard indicator. It distinguishes missing configuration, invalid configuration, and settings that pass local validation but have not had a live connection test. It never returns the API key or full provider URL and does not contact the provider. The AI checkbox is disabled when configuration is missing, invalid, or status cannot be retrieved; local template mode remains available. This is configuration readiness, not a successful live connection. See `docs/V3_65_AI_PROVIDER_STATUS.md`.


## v3.66 — Explicit AI provider connection test

Adds an administrator-only `POST /api/v1/system/ai-provider-test` endpoint and dashboard control for an explicit live connectivity probe. The probe sends only a short acknowledgement request to the configured provider, uses an 8-second timeout, does not include report notes, and returns safe metadata without model output, credentials, or provider URL. It records only the outcome and model label in the audit log. Missing/invalid configuration returns a safe unavailable response; provider failures return sanitized errors. Tests use a fake provider and do not prove that a real provider is connected. A successful response proves only that the configured endpoint returned a valid response at that moment. See `docs/V3_66_AI_PROVIDER_CONNECTION_TEST.md`.
