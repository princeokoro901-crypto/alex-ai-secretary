# Alex Core v3.19 — Trusted Connector Framework

## What changed

- Added `ConnectorManifest` to describe a connector's stable ID, namespaced action type, input fields, required fields, and allowed actor roles.
- Added `ConnectorRegistry`, which only accepts handlers registered by trusted application code. It rejects duplicate IDs/action types, unsupported field types, undeclared required fields, unknown input keys, missing required keys, wrong value types, unknown connectors, and disallowed roles.
- Connector execution is routed through the existing `ExecutionGateway`. Therefore a connector still requires an approval-bound, single-use execution permit for the exact mission/action; this registry does not issue approvals or permits.
- Added `GET /api/v1/connectors` to list sanitized connector manifests. Access is controlled by the `connectors:read` permission.
- No external connectors are installed or enabled by default. Registration is deliberately not exposed as a public API endpoint.

## Registering a connector in trusted application code

Create a `ConnectorManifest` and register it with `connector_registry` after the registry is initialized. Use a namespaced `action_type` such as `email.send`, declare only the input fields the connector needs, and keep credentials in deployment secrets/environment configuration rather than request payloads or manifest metadata. The handler must be a trusted function. Any external side effect must be covered by the matching approved action and a valid single-use permit.

## Current limitations

- Field validation is intentionally small and type-based, not a full JSON Schema implementation.
- This release does not include real email, WhatsApp, Microsoft Office, browser, payment, or other external integrations.
- The registry's allowed-role check is an additional guard, not a substitute for API authentication, endpoint permissions, approval, or connector-side authorization.
- The framework does not implement connector-specific idempotency, provider status lookup, rate limits, retries, or secret rotation. Do not automatically retry uncertain external actions.
- Production use still requires deployment hardening, external security review, and end-to-end tests against each provider's sandbox.
