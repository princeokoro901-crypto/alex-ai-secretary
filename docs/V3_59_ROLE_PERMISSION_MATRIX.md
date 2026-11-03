# v3.59 — Registered route-by-role permission matrix

## Change

Added `tests/test_role_permission_matrix.py`, a policy-contract suite that walks the registered `/api/v1/` routes and checks the effective permission for each HTTP method against the built-in administrator, operator, and reader grants. It also asserts that reader grants exclude execution, approval-decision, and administrator-only permissions; operator grants do not include approval decisions or `admin:all`; and unknown API routes continue to use the fail-closed administrator-only fallback.

Updated active application/test version expectations to 3.59. Existing route permissions, human approvals, execution permits, and connector safety gates were not changed.

## Validation

- Full Python test suite: recorded in `TEST_RESULTS.txt` after execution.
- Live role-access matrix: recorded separately; uses test-only credentials and disposable local stores.
- ZIP integrity and release-manifest hashes: recorded after packaging.

## Limits

The matrix test checks registered route policy and role grants, not successful endpoint behavior for every role. Live tests remain a finite set of safe local HTTP scenarios. Direct browser networking/CORS, TLS/reverse proxy, production deployment, and real external integrations are not validated by this release.
