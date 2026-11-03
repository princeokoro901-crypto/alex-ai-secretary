# Alex Core v3.12 — API Rate Limiting

## What changed

- Added a sliding-window request limiter to protected `/api/v1/*` routes.
- Applies an IP-address bucket before API-key authentication, helping limit credential guessing.
- Applies a second API-key-ID bucket after successful authentication, limiting one credential across client addresses.
- Returns HTTP `429` with a `Retry-After` header when a bucket is full.
- Configurable with `ALEX_RATE_LIMIT_REQUESTS` (default `120`) and `ALEX_RATE_LIMIT_WINDOW_SECONDS` (default `60`). Both values must be positive integers; invalid settings fail closed with HTTP `503`.
- Public `/health` is not rate-limited by this middleware.

## Important deployment limitation

The limiter is process-local and memory-based. It resets on restart and each application worker has its own counters. For multi-worker or multi-instance production deployments, enforce limits at a trusted API gateway or use a shared atomic store such as Redis. Proxy deployments must also configure trusted-proxy handling carefully; Alex currently uses the ASGI client host and does not trust arbitrary forwarded-IP headers.

Rate limiting complements, but does not replace, TLS, secure secret storage, key rotation/revocation, monitoring, and deployment-level network controls. Keep `ALEX_API_KEYS_JSON` configured before exposing the API to an untrusted network.
