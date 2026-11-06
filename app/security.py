"""API authentication, least-privilege authorization, and rate limiting for Alex.

Configure ALEX_API_KEYS_JSON as a JSON array of objects with key, key_id,
actor_id, and role fields. Optional ``active`` and ``permissions`` fields allow
operators to revoke keys and narrow role grants through their deployment secret
configuration. Never put raw keys in logs.

When no keys are configured, the app remains in local-development mode; do not
expose it publicly. Rate-limit counters are process-local; production deployments
need a trusted gateway or shared store.
"""
import hmac
import json
import os
import re
import time
from collections import deque
from threading import Lock
from fastapi import Request
from fastapi.responses import JSONResponse

PERMISSIONS = frozenset({
    "workforce:read",
    "workforce:write",
    "missions:read",
    "missions:write",
    "missions:execute",
    "missions:recover",
    "agents:execute",
    "actions:propose",
    "actions:execute",
    "orchestration:read",
    "orchestration:run",
    "orchestration:review",
    "audit:read",
    "approvals:read",
    "connectors:read",
    "approvals:decide",
    "system:read",
    "admin:all",
})

ROLE_PERMISSIONS = {
    "admin": PERMISSIONS,
    "operator": frozenset({
        "workforce:read", "workforce:write", "missions:read", "missions:write",
        "missions:execute", "missions:recover", "agents:execute", "actions:propose", "actions:execute",
        "orchestration:read", "orchestration:run", "orchestration:review", "system:read", "connectors:read",
    }),
    "reader": frozenset({
        "workforce:read", "missions:read", "orchestration:read", "system:read", "connectors:read",
    }),
}

# Endpoint-specific permission checks. Unknown protected routes fail closed and
# require the explicit admin:all grant. POST analytics routes are read operations
# and intentionally do not inherit a blanket "POST means write" rule.
_ROUTE_PERMISSIONS = (
    ("GET", re.compile(r"^/api/v1/workforce/agents$"), "workforce:read"),
    ("GET", re.compile(r"^/api/v1/workforce/teams$"), "workforce:read"),
    ("POST", re.compile(r"^/api/v1/workforce/teams$"), "workforce:write"),
    ("POST", re.compile(r"^/api/v1/workforce/delegate$"), "workforce:write"),
    ("GET", re.compile(r"^/api/v1/workforce/agents/[^/]+/performance$"), "workforce:read"),
    ("POST", re.compile(r"^/api/v1/workforce/(recommend|route)$"), "workforce:read"),
    ("POST", re.compile(r"^/api/v1/workspace/draft-report$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/workspace/ai-draft-report$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/workspace/create-reminder$"), "missions:write"),
    ("GET", re.compile(r"^/api/v1/workspace/reminders$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/missions$"), "missions:write"),
    ("GET", re.compile(r"^/api/v1/missions$"), "missions:read"),
    # This collection is a read-only view; the separate pending-audit queue and
    # decision endpoints require the stronger missions:recover permission.
    ("GET", re.compile(r"^/api/v1/missions/recovery-queue$"), "missions:read"),
    ("GET", re.compile(r"^/api/v1/missions/recovery-audit/pending$"), "missions:recover"),
    ("GET", re.compile(r"^/api/v1/missions/[^/]+$"), "missions:read"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/recovery-decision$"), "missions:recover"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/recovery-audit/retry$"), "missions:recover"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/agents$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/start$"), "missions:execute"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/agents/[^/]+/memory$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/agents/[^/]+/execute$"), "agents:execute"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/handoffs$"), "missions:write"),
    ("POST", re.compile(r"^/api/v1/missions/[^/]+/actions$"), "actions:propose"),
    ("GET", re.compile(r"^/api/v1/orchestrations$"), "orchestration:read"),
    ("GET", re.compile(r"^/api/v1/orchestrations/[^/]+$"), "orchestration:read"),
    ("POST", re.compile(r"^/api/v1/orchestrations/[^/]+/recovery-review$"), "orchestration:review"),
    ("POST", re.compile(r"^/api/v1/orchestrate$"), "orchestration:run"),
    ("GET", re.compile(r"^/api/v1/audit$"), "audit:read"),
    ("GET", re.compile(r"^/api/v1/connectors$"), "connectors:read"),
    # Gmail send-reconciliation views and mutations are administrator-only even
    # when the optional Gmail sender is disabled. Keep them explicit in the
    # endpoint permission inventory rather than relying on the fail-closed fallback.
    ("GET", re.compile(r"^/api/v1/connectors/gmail\.email/uncertain-sends$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/connectors/gmail\.email/reconciliation$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/connectors/gmail\.email/uncertain-sends/[^/]+/reconcile$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/email/providers$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/system/readiness$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/system/ai-provider-status$"), "system:read"),
    ("POST", re.compile(r"^/api/v1/system/ai-provider-test$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/email/google/oauth/start$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/email/google/accounts$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/email/google/accounts/[^/]+/refresh$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/email/google/accounts/[^/]+/disconnect$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/connectors/sandbox\.email/action-preview$"), "connectors:read"),
    ("POST", re.compile(r"^/api/v1/connectors/gmail\.email/action-preview$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/connectors/[^/]+/execute$"), "actions:execute"),
    ("GET", re.compile(r"^/api/v1/sandbox-email/outbox$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/executions$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/executions/uncertain$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/executions/[^/]+/reconciliations$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/executions/[^/]+/reconcile$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/executions/reconciliation-audit/pending$"), "admin:all"),
    ("POST", re.compile(r"^/api/v1/executions/reconciliations/[^/]+/audit/retry$"), "admin:all"),
    ("GET", re.compile(r"^/api/v1/approvals$"), "approvals:read"),
    ("GET", re.compile(r"^/api/v1/approvals/[^/]+$"), "approvals:read"),
    ("POST", re.compile(r"^/api/v1/approvals/[^/]+/decision$"), "approvals:decide"),
    ("POST", re.compile(r"^/api/v1/approvals/[^/]+/execution-permit$"), "actions:execute"),
    ("GET", re.compile(r"^/api/v1/(consistency|reconciliation)$"), "system:read"),
)

_RATE_BUCKETS: dict[str, deque] = {}
_RATE_LOCK = Lock()


def reset_rate_limiter_for_tests():
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()


def _rate_limited(bucket: str, limit: int, window_seconds: int, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    with _RATE_LOCK:
        timestamps = _RATE_BUCKETS.setdefault(bucket, deque())
        while timestamps and timestamps[0] <= current - window_seconds:
            timestamps.popleft()
        if len(timestamps) >= limit:
            return True
        timestamps.append(current)
        return False


def _rate_limit_settings() -> tuple[int, int]:
    try:
        limit = int(os.getenv("ALEX_RATE_LIMIT_REQUESTS", "120"))
        window = int(os.getenv("ALEX_RATE_LIMIT_WINDOW_SECONDS", "60"))
    except ValueError as exc:
        raise RuntimeError("Rate-limit settings must be integers") from exc
    if limit < 1 or window < 1:
        raise RuntimeError("Rate-limit settings must be positive integers")
    return limit, window


def load_api_keys(raw: str | None = None) -> list[dict]:
    raw = os.getenv("ALEX_API_KEYS_JSON") if raw is None else raw
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ALEX_API_KEYS_JSON must be valid JSON") from exc
    if not isinstance(entries, list):
        raise RuntimeError("ALEX_API_KEYS_JSON must be a JSON array")

    seen_ids = set()
    normalized = []
    for entry in entries:
        required = ("key", "key_id", "actor_id", "role")
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), str) and entry[k].strip() for k in required
        ):
            raise RuntimeError("Each API key entry needs non-empty key, key_id, actor_id, and role strings")
        if entry["key_id"] in seen_ids:
            raise RuntimeError("API key identifiers must be unique")
        if entry["role"] not in ROLE_PERMISSIONS:
            raise RuntimeError("API key role must be admin, operator, or reader")

        active = entry.get("active", True)
        if not isinstance(active, bool):
            raise RuntimeError("API key active field must be a boolean")

        explicit_permissions = entry.get("permissions")
        if explicit_permissions is not None:
            if not isinstance(explicit_permissions, list) or any(
                not isinstance(permission, str) or permission not in PERMISSIONS
                for permission in explicit_permissions
            ):
                raise RuntimeError("API key permissions must be an array of known permission strings")
            if len(set(explicit_permissions)) != len(explicit_permissions):
                raise RuntimeError("API key permissions must not contain duplicates")
            if not set(explicit_permissions).issubset(ROLE_PERMISSIONS[entry["role"]]):
                raise RuntimeError("Explicit permissions cannot exceed the key role's permission ceiling")
            explicit_permissions = frozenset(explicit_permissions)

        seen_ids.add(entry["key_id"])
        normalized.append({
            "key": entry["key"],
            "key_id": entry["key_id"],
            "actor_id": entry["actor_id"],
            "role": entry["role"],
            "active": active,
            "permissions": explicit_permissions,
        })
    return normalized


def authenticate_key(presented: str, entries: list[dict]):
    match = None
    # Compare all active keys rather than exiting at the first match.
    for entry in entries:
        if entry.get("active", True) and hmac.compare_digest(presented, entry["key"]):
            match = entry
    return match


def _required_permission(method: str, path: str) -> str:
    for allowed_method, pattern, permission in _ROUTE_PERMISSIONS:
        if method == allowed_method and pattern.fullmatch(path):
            return permission
    return "admin:all"


async def authenticate_api_request(request: Request, call_next):
    """Authenticate API calls and enforce endpoint-level permissions."""
    if not request.url.path.startswith("/api/v1/"):
        return await call_next(request)

    client_host = request.client.host if request.client else "unknown"
    try:
        limit, window = _rate_limit_settings()
    except RuntimeError:
        return JSONResponse(status_code=503, content={"detail": "API security configuration is invalid"})

    if _rate_limited(f"ip:{client_host}", limit, window):
        return JSONResponse(status_code=429, content={"detail": "API rate limit exceeded"}, headers={"Retry-After": str(window)})

    try:
        entries = load_api_keys()
    except RuntimeError:
        return JSONResponse(status_code=503, content={"detail": "API authentication configuration is invalid"})

    if not entries:
        # Convenient for local tests/development only; not safe for public exposure.
        request.state.actor = {
            "actor_id": "local:owner", "key_id": "local", "role": "admin",
            "permissions": sorted(ROLE_PERMISSIONS["admin"]),
        }
        return await call_next(request)

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    entry = authenticate_key(token, entries) if scheme.lower() == "bearer" and token else None
    if entry is None:
        return JSONResponse(status_code=401, content={"detail": "Valid bearer API key required"}, headers={"WWW-Authenticate": "Bearer"})

    granted = entry["permissions"] if entry["permissions"] is not None else ROLE_PERMISSIONS[entry["role"]]
    request.state.actor = {
        "actor_id": entry["actor_id"],
        "key_id": entry["key_id"],
        "role": entry["role"],
        "permissions": sorted(granted),
    }

    if _rate_limited(f"key:{entry['key_id']}", limit, window):
        return JSONResponse(status_code=429, content={"detail": "API rate limit exceeded"}, headers={"Retry-After": str(window)})

    required = _required_permission(request.method, request.url.path)
    if "admin:all" not in granted and required not in granted:
        return JSONResponse(status_code=403, content={"detail": "API key lacks the permission required for this endpoint"})
    return await call_next(request)
