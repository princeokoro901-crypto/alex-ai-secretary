# v3.56 — Explicit API route permission coverage

## Change

The API authorization middleware intentionally fails closed for any `/api/v1/` request without a matching route rule. In v3.55, three Gmail send-reconciliation endpoints were protected by that fallback and also checked administrator role inside their handlers, but they were not explicit entries in the permission inventory. v3.56 adds explicit `admin:all` entries for:

- `GET /api/v1/connectors/gmail.email/uncertain-sends`
- `GET /api/v1/connectors/gmail.email/reconciliation`
- `POST /api/v1/connectors/gmail.email/uncertain-sends/{attempt_id}/reconcile`

The effective protection is not weakened; the inventory now describes the intended policy directly.

## New contract test

`tests/test_route_permission_coverage.py` verifies that every registered `/api/v1/` method/path has an explicit rule, overlapping rules do not assign conflicting permissions, all referenced permission names exist, and each rule still maps to a registered route. This is a static route-table contract. It does not prove every handler's business logic or every role's behavior. The existing live role matrix remains the runtime authorization check.

Run:

```bash
python -m pytest -q
python scripts/live_role_access_matrix_test.py
```

All tests use local application code and disposable test data. No real provider is contacted and no Gmail message is sent. Direct browser networking, production TLS/reverse-proxy behavior, and production deployment remain untested.

## Next

Expand live allow/deny matrices for execution, approval, execution-permit, connector execution, and reconciliation routes. Use safe invalid/no-op requests and keep all real provider integrations disabled.
