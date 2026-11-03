# v3.65 — AI provider configuration status

## Behavior

- `GET /api/v1/system/ai-provider-status` requires `system:read` and returns configuration status only.
- States: `not_configured`, `invalid_configuration`, or `configured_not_tested`.
- The response never includes the API key or provider URL. When configured, it includes a bounded model label.
- The endpoint does not contact the provider. `connection_tested` is always `false`.
- The dashboard makes this distinction explicit and disables AI mode when configuration is absent/invalid or status cannot be fetched.
- Local-template report drafting remains available independently of AI configuration.

## Validation scope

Automated tests cover configuration state, safe response fields, route authorization, dashboard status behavior, and version reporting. No real AI provider is contacted by these tests. A future connection-test action must be explicitly initiated by an administrator and must use a strict timeout and sanitized errors.
