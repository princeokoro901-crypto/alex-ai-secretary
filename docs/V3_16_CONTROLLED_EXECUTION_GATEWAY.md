# Alex Core v3.16 — Controlled Execution Gateway

## Summary

Adds an internal execution gateway that connects the v3.15 approval-bound, single-use permit service to trusted application handlers. The gateway is initialized alongside the existing approval and audit services.

## Behavior

- Handlers are registered only by trusted application code; request payloads cannot register or replace handlers.
- Unknown action types are rejected before permit consumption.
- The gateway consumes the permit against the exact mission/action immediately before invoking the handler.
- A successful or failed handler outcome is captured and attributed to the authenticated actor in the audit log.
- A handler exception is recorded as a failed execution and is not automatically retried. The permit remains consumed to avoid unsafe duplicate side effects.
- Duplicate handler registration is rejected.

## Important limitations

- No email, WhatsApp, Office, calling, or other external connector is registered in this release.
- The gateway is an internal service, not a public generic execution endpoint. A connector must be added explicitly in trusted application code.
- Execution results are currently in memory; durable execution journaling is a future milestone.
- Permit consumption and a remote side effect cannot share one atomic transaction. A crash after permit consumption but before/during the side effect can leave an ambiguous outcome. Future connectors need idempotency keys, connector-side reconciliation, and explicit unknown-outcome handling.
- This is a development milestone, not a production security certification.

## Tests

The new tests cover trusted-handler registration, exact action/permit validation, one-time use, handler exceptions, audit outcome attribution, and unknown handler rejection. The complete test suite and extracted archive are validated as part of the release process.
