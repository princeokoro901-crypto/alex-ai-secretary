"""Trusted-code connector registry layered over Alex's controlled execution gateway.

Connectors are registered by application code, never by request payloads. This
module validates connector metadata and inputs; it does not bypass approvals.
"""
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from app.execution import ExecutionError, ExecutionGateway, ExecutionResult

_ALLOWED_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


@dataclass(frozen=True)
class ConnectorManifest:
    connector_id: str
    name: str
    description: str
    action_type: str
    input_fields: dict[str, str]
    required_fields: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ("operator", "admin")

    def public_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "name": self.name,
            "description": self.description,
            "action_type": self.action_type,
            "input_fields": dict(self.input_fields),
            "required_fields": list(self.required_fields),
            "allowed_roles": list(self.allowed_roles),
            "execution_policy": "approval-bound single-use permit required",
        }


class ConnectorRegistry:
    """Register trusted connector handlers and route them through ExecutionGateway."""

    def __init__(self, gateway: ExecutionGateway):
        self.gateway = gateway
        self._manifests: dict[str, ConnectorManifest] = {}
        self._by_action: dict[str, ConnectorManifest] = {}
        self._action_builders: dict[str, Callable[[dict], str]] = {}
        self._lock = RLock()

    def register(self, manifest: ConnectorManifest, handler: Callable[..., Any],
                 action_builder: Callable[[dict], str] | None = None,
                 action_aware_handler: bool = False,
                 execution_context_handler: bool = False) -> None:
        if not isinstance(manifest, ConnectorManifest):
            raise ValueError("A ConnectorManifest is required")
        for field_name, value in (("connector_id", manifest.connector_id), ("name", manifest.name),
                                  ("description", manifest.description), ("action_type", manifest.action_type)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
        if "." not in manifest.action_type or any(not part for part in manifest.action_type.split(".", 1)):
            raise ValueError("action_type must be namespaced, e.g. email.send")
        if not callable(handler):
            raise ValueError("Connector handler must be callable")
        if action_builder is not None and not callable(action_builder):
            raise ValueError("action_builder must be callable when supplied")
        if not isinstance(action_aware_handler, bool):
            raise ValueError("action_aware_handler must be a boolean")
        if not isinstance(execution_context_handler, bool):
            raise ValueError("execution_context_handler must be a boolean")
        if action_aware_handler and action_builder is None:
            raise ValueError("An action-aware handler requires an action_builder")
        if not isinstance(manifest.input_fields, dict):
            raise ValueError("input_fields must be a mapping of field names to supported types")
        for name, field_type in manifest.input_fields.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(field_type, str) or field_type not in _ALLOWED_TYPES:
                raise ValueError(f"Unsupported input field declaration: {name!r}")
        if len(set(manifest.required_fields)) != len(manifest.required_fields):
            raise ValueError("required_fields cannot contain duplicates")
        if not set(manifest.required_fields).issubset(manifest.input_fields):
            raise ValueError("Every required field must be declared in input_fields")
        if not manifest.allowed_roles or not set(manifest.allowed_roles).issubset({"admin", "operator", "reader"}):
            raise ValueError("allowed_roles must contain one or more known roles")
        with self._lock:
            if manifest.connector_id in self._manifests:
                raise ValueError("Connector ID is already registered")
            if manifest.action_type in self._by_action:
                raise ValueError("Action type is already registered")
            # Gateway rejects duplicate action types too. Do this before mutating our own maps.
            self.gateway.register(manifest.action_type, handler, receives_action=action_aware_handler,
                                  receives_context=execution_context_handler)
            self._manifests[manifest.connector_id] = manifest
            self._by_action[manifest.action_type] = manifest
            if action_builder is not None:
                self._action_builders[manifest.connector_id] = action_builder

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._manifests[key].public_dict() for key in sorted(self._manifests)]

    def execute(self, *, connector_id: str, permit_id: str, mission_id: str, action: str,
                payload: dict, actor: dict) -> ExecutionResult:
        with self._lock:
            manifest = self._manifests.get(connector_id)
        if manifest is None:
            raise ExecutionError("Unknown connector")
        if not isinstance(actor, dict) or actor.get("role") not in manifest.allowed_roles:
            raise ExecutionError("Actor role is not allowed to use this connector")
        if not isinstance(payload, dict):
            raise ExecutionError("Connector input must be an object")
        unknown = set(payload) - set(manifest.input_fields)
        if unknown:
            raise ExecutionError("Unknown connector input field(s): " + ", ".join(sorted(unknown)))
        missing = set(manifest.required_fields) - set(payload)
        if missing:
            raise ExecutionError("Missing required connector input field(s): " + ", ".join(sorted(missing)))
        for name, value in payload.items():
            expected = _ALLOWED_TYPES[manifest.input_fields[name]]
            # bool is a subclass of int in Python, so reject it for numeric fields explicitly.
            if manifest.input_fields[name] in {"integer", "number"} and isinstance(value, bool):
                raise ExecutionError(f"Connector input field '{name}' has the wrong type")
            if not isinstance(value, expected):
                raise ExecutionError(f"Connector input field '{name}' has the wrong type")
        action_builder = self._action_builders.get(connector_id)
        if action_builder is not None:
            try:
                expected_action = action_builder(dict(payload))
            except (TypeError, ValueError) as exc:
                raise ExecutionError(f"Connector could not bind action to payload: {exc}") from exc
            if not isinstance(expected_action, str) or action != expected_action:
                raise ExecutionError("Approved action does not match this connector payload")
        return self.gateway.execute(permit_id=permit_id, mission_id=mission_id, action=action,
                                   action_type=manifest.action_type, payload=payload, actor=actor)
