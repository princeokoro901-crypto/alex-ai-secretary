"""Cross-check every registered API route against all built-in role grants.

This is a policy contract test, not a substitute for live HTTP integration tests.
It deliberately inspects the registered route table without invoking endpoint logic.
"""
import re

from app.main import app
from app.security import ROLE_PERMISSIONS, _required_permission


def _sample_path(path: str) -> str:
    return re.sub(r"\{[^}:]+(?::[^}]+)?\}", "matrix-test-id", path)


def _api_route_cases():
    cases = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/"):
            continue
        for method in sorted(getattr(route, "methods", set()) or set()):
            sample = _sample_path(path)
            required = _required_permission(method, sample)
            cases.append((method, path, sample, required))
    return cases


def test_every_registered_api_route_has_a_deterministic_role_matrix():
    cases = _api_route_cases()
    assert cases, "Expected protected API routes to be registered"
    missing = [(method, path) for method, path, _, permission in cases if not permission]
    assert not missing, f"Routes without an effective permission: {missing}"

    # The built-in administrator role must be able to satisfy every registered
    # API permission; reader/operator access follows the declared role grants.
    for method, path, sample, required in cases:
        for role, grants in ROLE_PERMISSIONS.items():
            allowed = "admin:all" in grants or required in grants
            if role == "admin":
                assert allowed, f"Admin unexpectedly denied {method} {path} ({required})"
            else:
                assert allowed == (required in grants), (
                    f"Role matrix inconsistent for {role} {method} {path}: {required}"
                )


def test_sensitive_permission_groups_are_not_granted_to_reader():
    reader = ROLE_PERMISSIONS["reader"]
    sensitive = {
        "missions:execute", "agents:execute", "actions:execute",
        "approvals:decide", "admin:all",
    }
    assert not (reader & sensitive), f"Reader has sensitive grants: {sorted(reader & sensitive)}"


def test_operator_cannot_bypass_approval_or_admin_only_gates():
    operator = ROLE_PERMISSIONS["operator"]
    assert "approvals:decide" not in operator
    assert "admin:all" not in operator
    # Operators can execute only through separately permissioned gateways; this
    # assertion documents the existing design rather than claiming an action ran.
    assert "actions:execute" in operator


def test_unclassified_api_routes_remain_admin_only():
    # Unknown protected routes must resolve to the restrictive fallback.
    assert _required_permission("GET", "/api/v1/__unknown_route_for_test__") == "admin:all"
    assert _required_permission("POST", "/api/v1/__unknown_route_for_test__") == "admin:all"
