# v3.32 — Read-only browser console

## What changed

Alex now serves a small, responsive, same-origin dashboard at `/`. The page gives an operator a quick view of:

- public health/version and runtime-reported orchestration/audit indicators;
- trusted-code connector inventory;
- pending approval records;
- the administrator-only, redacted system-readiness report.

The dashboard is deliberately read-only. It does not create missions, make approval decisions, issue execution permits, run connectors, connect a Google account, toggle feature flags, or send email. Consequential actions remain on the existing authenticated API workflow and still require the existing approval-bound single-use execution permit.

## API key handling

The API key field is a password input. If the user chooses Connect, the key is stored in `sessionStorage` for the current browser tab only, and is sent only to same-origin `/api/v1/` endpoints in the `Authorization: Bearer` header. The page does not use `localStorage`, third-party assets, analytics, or external API calls. The Clear key button removes the session value.

A user should use only a trusted device and clear the key before handing the device to someone else. Browser extensions or malware on the device are outside the protection offered by this page. If API keys are not configured, the backend's existing local-development behavior can grant broad local access; do not expose such a deployment to a network.

## Response safety

The dashboard inserts API-returned text with DOM `textContent`, not HTML parsing. The root page is sent with `Cache-Control: no-store`, a same-origin Content Security Policy, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`. The API endpoints retain their existing authentication and authorization middleware.

## Run locally

From the project root after installing `requirements.txt`:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The API docs remain available at `/docs`; the read-only setup preflight remains `python -m app.setup_check`.

## Validation and limits

Automated tests verify dashboard and asset routes, security headers, same-origin assets, session-only key storage, the absence of inline scripts, and the version health response. The Python test suite does not perform real browser automation, production deployment checks, or live Google requests. No live credentials or external email delivery are involved in this milestone.
