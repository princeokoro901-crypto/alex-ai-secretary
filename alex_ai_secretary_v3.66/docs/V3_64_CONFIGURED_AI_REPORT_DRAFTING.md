# v3.64 — Configured AI report drafting

## What changed

- Added `app/llm_provider.py`, a provider-neutral adapter for OpenAI-compatible `/chat/completions` endpoints.
- Added `app/ai_workspace.py` to build a bounded report prompt and return an AI-generated Markdown draft with a review warning, SHA-256 digest, and output integrity checks.
- Added `POST /api/v1/workspace/ai-draft-report`, protected by `missions:write`.
- Added an explicit dashboard checkbox. Local template mode remains the default; when AI mode is selected, the user is informed that notes may be sent to the configured provider.
- The API key is read only from the server environment and is never returned to the browser or audit metadata.
- Provider output is treated as untrusted text and rendered as text in the dashboard. No external tools or consequential actions are available to the model through this adapter.

## Configuration

Set these environment variables on the server, not in browser code or checked-in files:

- `ALEX_LLM_BASE_URL`: HTTPS base URL ending in the provider API prefix, for example a provider's `/v1` base URL.
- `ALEX_LLM_API_KEY`: secret key for that provider.
- `ALEX_LLM_MODEL`: model identifier supported by the provider.
- Optional `ALEX_LLM_ALLOW_INSECURE_LOCAL=true`: permits plain HTTP only when the configured hostname is `localhost`, `127.0.0.1`, or `::1`; use only for a local development model server.

If any required setting is missing, AI mode returns HTTP 503 without falling back silently to the local template. Provider/network/protocol errors return a sanitized HTTP 502. The timeout is 25 seconds and response size is bounded.

## Data and safety notes

Selecting AI mode sends the title, audience, and source notes to the configured model provider. Operators must review that provider's data handling and retention terms before using sensitive content. The generated report is explicitly marked as an AI draft and not independently fact-checked. The model cannot send email, browse the web, execute missions, or perform other external actions through this endpoint.

## Validation and limitations

- Tests use an injected fake transport; they do not call a live model provider.
- Verified configuration fail-closed behavior, HTTPS enforcement, sanitized invalid responses, report labeling/output checks, role denial, dashboard opt-in control, and release health version.
- A real provider key and model have not been configured or validated in this release.
- Direct browser networking/CORS and production deployment remain separate validation tasks.
