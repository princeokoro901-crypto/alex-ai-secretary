# v3.31 — First-run setup preflight and end-to-end integration testing

## Purpose

Make Alex easier to prepare for local development and validate that the existing Gmail components work together through the real application API routes, without needing Google credentials or sending external mail.

## First-run preflight

Run from the project root:

```bash
python -m app.setup_check
```

For machine-readable output:

```bash
python -m app.setup_check --json
```

A convenience wrapper is also available:

```bash
python scripts/alex_setup_check.py
```

The checker is read-only. It does not create files, test a live provider, start OAuth, approve actions, enable any feature, or send requests. It never prints environment values, API keys, OAuth secrets, encryption keys, or configured paths. It validates the canonical API-key configuration parser, boolean feature flags, OAuth required settings, Fernet key format, redirect URI shape, Gmail feature-gate relationships, required persistent-store settings, and whether configured storage paths appear writable.

When Gmail sending is disabled, the report targets `sandbox_only` mode and warns that the Gmail sender is disabled. That is a valid local-development configuration. A successful checker exit is not a production-readiness claim; the existing administrator-only `/api/v1/system/readiness` endpoint remains a separate runtime diagnostic.

If Gmail sending is explicitly requested, missing keys, missing persistent storage settings, incompatible feature flags, an invalid encryption key, or an unsafe redirect URI are reported as errors. The checker does not change configuration or enable Gmail.

## End-to-end tests

`tests/test_gmail_end_to_end.py` exercises the application API with a fake Google transport:

1. Build an account/content-bound action preview.
2. Create a mission and propose the exact action as an operator.
3. Approve it with a separate administrator actor.
4. Issue a single-use execution permit.
5. Execute through the registered connector and controlled gateway.
6. Verify the send ledger and durable execution journal.

Additional cases verify that a successful replay with the same idempotency key does not make a second provider call, and that a changed payload is rejected before the fake provider is called.

These are integration tests of the application code and route wiring, not a live Google integration test. All provider interactions are simulated; no external Google requests or email delivery occur.

## Safety and architecture

- Human approval remains required for Gmail execution.
- Approval is performed by a separate actor from the requester.
- The permit remains single-use and action-bound.
- Gmail remains disabled by default in normal configuration.
- Ambiguous external outcomes are not automatically retried.
- The setup checker does not mutate configuration or operational state.
- Existing workforce, orchestration, approval, connector, audit, and journal architecture is preserved.

## Validation

- Baseline v3.30: 204 tests passed when run with `PYTHONPATH=.` from the project root.
- Added setup-check tests: 7.
- Added API-level Gmail integration tests: 3.
- Final validation is recorded in `TEST_RESULTS.txt`.
