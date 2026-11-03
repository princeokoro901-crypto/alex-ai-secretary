# Alex Core v3.9 — Durable Security Audit Hardening

## Changes

- Audit details are recursively sanitized before persistence. Fields whose names indicate passwords, secrets, tokens, API keys, authorization headers, credentials, or private keys are replaced with `[REDACTED]`, including nested dictionaries and lists.
- Durable audit writes are completed and `fsync`ed before the event is added to in-memory history. A failed write therefore does not advance the in-memory hash chain.
- Updated application title and health version to `3.9`.
- Added tests for recursive redaction, hash-chain verification after reload, and failed-write behavior.

## Boundaries and known risks

- SHA-256 hash chaining detects accidental or unsophisticated edits, but it is not a digital signature and does not prevent an attacker with file access from rewriting the whole chain.
- Redaction is key-name based. Callers must not place raw secrets inside ordinary fields such as `message`, `input`, or `details`; avoid recording raw request bodies.
- This release does not implement an external secret manager, key rotation, rate limiting, multi-process file locking, or a production identity provider. No external credentials or approvals are assumed.
- A process crash during a filesystem write can still leave a partial final JSONL line; startup currently quarantines a malformed audit file rather than automatically repairing it.

## Validation

Run from the extracted project root:

```bash
PYTHONPATH=. pytest -q
```

Audit-only tests can be run with `PYTHONPATH=. pytest -q tests/test_audit.py`.
