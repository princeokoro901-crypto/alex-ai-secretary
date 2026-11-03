"""Controlled execution gateway for approval-bound actions.

Handlers are registered by trusted application code, never by API payloads.
The gateway consumes a single-use approval permit immediately before invoking
an action handler. A handler failure is recorded and the permit stays consumed:
external side effects cannot be made atomic with the local approval store.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable

from app.approvals import ApprovalError, ApprovalService, ApprovalStoreError


class ExecutionError(RuntimeError):
    """The gateway could not safely execute the requested action."""


class ExecutionOutcomeUncertain(RuntimeError):
    """A handler cannot prove whether its external side effect completed."""


@dataclass(frozen=True)
class ExecutionContext:
    """Trusted gateway-generated context for correlating external side effects."""
    execution_id: str
    permit_id: str
    mission_id: str
    action_digest: str
    actor_id: str
    key_id: str | None
    action_type: str


@dataclass
class ExecutionResult:
    execution_id: str
    permit_id: str
    mission_id: str
    action_digest: str
    status: str
    started_at: str
    finished_at: str
    result: Any = None
    error: str | None = None


class ExecutionGateway:
    def __init__(self, approvals: ApprovalService,
                 audit: Callable[..., Any] | None = None, journal=None):
        self.approvals = approvals
        self.journal = journal
        self.audit = audit
        self._handlers: dict[str, tuple[Callable[..., Any], bool, bool]] = {}
        self._lock = RLock()
        self._results: dict[str, ExecutionResult] = {}

    def register(self, action_type: str, handler: Callable[..., Any], *, receives_action: bool = False,
                 receives_context: bool = False) -> None:
        """Register trusted code for an explicit action type."""
        if not isinstance(action_type, str) or not action_type.strip():
            raise ValueError("Action type is required")
        if not callable(handler):
            raise ValueError("Handler must be callable")
        if not isinstance(receives_action, bool):
            raise ValueError("receives_action must be a boolean")
        if not isinstance(receives_context, bool):
            raise ValueError("receives_context must be a boolean")
        with self._lock:
            if action_type in self._handlers:
                raise ValueError("A handler is already registered for this action type")
            self._handlers[action_type] = (handler, receives_action, receives_context)

    def execute(self, *, permit_id: str, mission_id: str, action: str,
                action_type: str, payload: dict, actor: dict) -> ExecutionResult:
        if not isinstance(payload, dict):
            raise ExecutionError("Execution payload must be an object")
        if not isinstance(action_type, str) or not action_type.strip():
            raise ExecutionError("Action type is required")
        with self._lock:
            registered = self._handlers.get(action_type)
        if registered is None:
            raise ExecutionError("No trusted handler is registered for this action type")
        handler, receives_action, receives_context = registered

        started = datetime.now(timezone.utc).isoformat()
        try:
            # Consumption persists before the handler can perform its side effect.
            approval = self.approvals.consume_execution_permit(
                permit_id, mission_id, action, actor)
        except (ApprovalError, ApprovalStoreError) as exc:
            raise ExecutionError(str(exc)) from exc

        import uuid
        execution_id = str(uuid.uuid4())
        if self.journal is not None:
            try:
                self.journal.begin(execution_id, permit_id, mission_id, approval.action_digest, action_type, actor)
            except Exception as exc:
                raise ExecutionError("Could not durably journal execution; handler was not called") from exc
        context = ExecutionContext(
            execution_id=execution_id,
            permit_id=permit_id,
            mission_id=mission_id,
            action_digest=approval.action_digest,
            actor_id=str(actor.get("actor_id", "unknown")),
            key_id=actor.get("key_id"),
            action_type=action_type,
        )
        try:
            if receives_action and receives_context:
                result = handler(dict(payload), action, context)
            elif receives_action:
                result = handler(dict(payload), action)
            elif receives_context:
                result = handler(dict(payload), context)
            else:
                result = handler(dict(payload))
            record = ExecutionResult(execution_id, permit_id, mission_id,
                approval.action_digest, "succeeded", started,
                datetime.now(timezone.utc).isoformat(), result=result)
        except ExecutionOutcomeUncertain as exc:
            record = ExecutionResult(execution_id, permit_id, mission_id,
                approval.action_digest, "uncertain", started,
                datetime.now(timezone.utc).isoformat(), error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # known/ordinary connector failures are captured; no retry
            record = ExecutionResult(execution_id, permit_id, mission_id,
                approval.action_digest, "failed", started,
                datetime.now(timezone.utc).isoformat(), error=f"{type(exc).__name__}: {exc}")

        if self.journal is not None:
            try:
                self.journal.finish(execution_id, record.status, result=record.result, error=record.error)
            except Exception as exc:
                # The durable row remains running and is recovered as uncertain on restart.
                raise ExecutionError("Handler returned but durable outcome could not be recorded; reconcile before retrying") from exc
        with self._lock:
            self._results[execution_id] = record
        if self.audit:
            self.audit(
                ("execution_succeeded" if record.status == "succeeded" else
                 "execution_uncertain" if record.status == "uncertain" else "execution_failed"),
                str(actor.get("actor_id", "unknown")), "execution", execution_id,
                {"actor_id": actor.get("actor_id"), "key_id": actor.get("key_id"),
                 "role": actor.get("role"), "permit_id": permit_id,
                 "mission_id": mission_id, "action_digest": approval.action_digest,
                 "status": record.status},
            )
        return record

    def get(self, execution_id: str) -> ExecutionResult | None:
        with self._lock:
            return self._results.get(execution_id)

    def list(self) -> list[ExecutionResult]:
        with self._lock:
            return list(self._results.values())
