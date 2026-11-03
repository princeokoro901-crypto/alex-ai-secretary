# Alex Core v3.6 — Audit Integrity and Recovery

This release adds a SHA-256 hash chain to JSONL audit events. Each event stores `prev_hash` and `event_hash`; startup verifies the chain and quarantines a malformed or tampered file as `<path>.corrupt` instead of loading untrusted history. `GET /health` reports `audit_integrity` and `audit_recovery`. Existing audit filtering and append-only persistence remain unchanged.

This is tamper detection, not tamper prevention or cryptographic signing. Key rotation, rate limiting, and external integrations remain out of scope.
