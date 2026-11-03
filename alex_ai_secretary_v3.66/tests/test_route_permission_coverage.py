"""Contract tests: every protected API route must have an explicit permission rule."""
import re

from app.main import app
from app.security import PERMISSIONS, _ROUTE_PERMISSIONS


def _sample_path(path: str) -> str:
    # FastAPI route parameters are replaced with safe, single-segment examples.
    return re.sub(r"\{[^}:]+(?::[^}]+)?\}", "test-id", path)


def test_every_registered_api_route_has_an_explicit_permission_rule():
    registered = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/"):
            continue
        sample = _sample_path(path)
        for method in sorted(getattr(route, "methods", set()) or set()):
            matches = [
                permission
                for allowed_method, pattern, permission in _ROUTE_PERMISSIONS
                if allowed_method == method and pattern.fullmatch(sample)
            ]
            assert matches, (
                f"Missing explicit permission rule for {method} {path}; "
                "protected routes must not depend on the admin:all fallback"
            )
            assert len(set(matches)) == 1, (
                f"Conflicting permission rules for {method} {path}: {matches}"
            )
            registered.append((method, sample))
    assert registered, "Expected to inspect at least one protected API route"


def test_permission_inventory_uses_known_permissions_only():
    assert _ROUTE_PERMISSIONS, "Permission inventory must not be empty"
    unknown = sorted({permission for _, _, permission in _ROUTE_PERMISSIONS} - PERMISSIONS)
    assert not unknown, f"Unknown permissions in route inventory: {unknown}"


def test_every_permission_rule_matches_a_registered_api_route():
    registered = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/"):
            registered.extend(
                (method, _sample_path(path))
                for method in (getattr(route, "methods", set()) or set())
            )
    stale = []
    for method, pattern, permission in _ROUTE_PERMISSIONS:
        if not any(
            registered_method == method and pattern.fullmatch(sample)
            for registered_method, sample in registered
        ):
            stale.append((method, pattern.pattern, permission))
    assert not stale, f"Permission rules no longer mapped to an API route: {stale}"
