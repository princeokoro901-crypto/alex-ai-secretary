# Alex Core v3.13 — Fine-Grained API Access Control

## What changed

- Replaced the broad rule “readers cannot use modifying HTTP methods” with endpoint-specific permission checks. This matters because analytical routes such as `/api/v1/workforce/recommend` use POST without modifying resources.
- Added permission groups for workforce reads/writes, mission reads/writes/execution, agent execution, action proposals, orchestration reads/runs, audit reads, and system reports.
- Preserved the three roles. `admin` receives all permissions by default; `operator` can operate Alex but cannot read the audit log by default; `reader` receives read-only permissions.
- Added optional per-key `permissions` to narrow a role's default grants. Explicit permissions cannot exceed the role's maximum grant set; omitted permissions preserve role defaults.
- Added optional `active` (default `true`) to API-key entries. Setting a key to `false` revokes it once the updated configuration is applied to the running process. In typical environment-variable deployments, that means a restart or redeploy.
- Unknown protected routes fail closed unless the key has the `admin:all` permission.
- Middleware attaches actor ID, key ID, role, and effective permissions to `request.state.actor` for later audit attribution work.

## Configuration example

```json
[
  {
    "key": "replace-with-a-long-random-secret",
    "key_id": "office-reader-01",
    "actor_id": "office-user-01",
    "role": "reader",
    "active": true,
    "permissions": ["workforce:read", "missions:read"]
  },
  {
    "key": "replace-with-another-long-random-secret",
    "key_id": "office-operator-01",
    "actor_id": "office-user-02",
    "role": "operator",
    "active": true
  }
]
```

Supply the JSON through `ALEX_API_KEYS_JSON` using the deployment's secret/configuration facility. Do not commit live keys to source control or paste them into logs. To rotate a key, deploy a new key entry and remove/disable the old one; to revoke a key, set `active` to `false` and deploy the updated configuration. Environment variables are not normally refreshed in a running process by the hosting platform, so plan for a restart/redeploy unless the deployment explicitly supports live configuration reloads. There is intentionally no unauthenticated key-management endpoint.

## Authorization behavior

- `GET /api/v1/audit` requires `audit:read`, granted by default only to admins.
- Mission execution, agent execution, orchestration, and action proposals have separate permissions.
- Analytics routes using POST are authorized by their specific read permission, not by HTTP method alone.
- Unknown protected paths and unsupported method/path combinations require `admin:all` and otherwise fail closed.
- If the configured JSON is malformed, contains invalid permissions, or attempts role escalation, requests fail closed with HTTP 503.

## Validation

The automated suite covers revoked and active keys, role boundaries, endpoint-specific permissions, narrowed grants, escalation rejection, unknown-route denial, and existing rate-limit/authentication behavior.

## Remaining security work

- Write actor/key IDs into durable audit events for sensitive operations and access denials.
- Add dedicated key-rotation runbook/secret-manager integration and operational monitoring.
- Add human approval gates for consequential actions; permission to propose an action is not approval to execute it.
- Use TLS, a secret manager, network controls, and a shared rate limiter/API gateway in production. Local-development mode remains enabled when `ALEX_API_KEYS_JSON` is unset and must never be exposed to untrusted networks.
