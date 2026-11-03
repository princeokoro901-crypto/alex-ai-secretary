# v3.66 — Explicit AI provider connection test

## Changes

- Added an administrator-only `POST /api/v1/system/ai-provider-test` endpoint.
- Added a dashboard button to run a deliberate live connectivity probe after server-side configuration passes local validation.
- The probe sends a small acknowledgement prompt only; report title, audience, and notes are not included.
- Uses an 8-second request timeout and a small output-token limit. Redirects remain disabled by the existing HTTP transport.
- Does not return provider-generated content, API keys, or provider URLs. Audit metadata records only the outcome and configured model label.
- Provider configuration status remains separate from the one-off live test result. After a page reload, status still reports configured-but-not-tested because the live result is not persisted as a long-lived health guarantee.

## Validation and limitations

- Automated tests mock the transport. They validate request shape, timeout, authorization boundary, response sanitization, and unavailable/error behavior.
- A successful live test would prove only that the configured endpoint returned a valid response at that moment. It does not validate report quality, provider privacy terms, production network configuration, or availability over time.
- No real provider credentials were used for release testing; live connectivity is not claimed.
- The dashboard test control is visible but the endpoint enforces administrator-only access server-side.
