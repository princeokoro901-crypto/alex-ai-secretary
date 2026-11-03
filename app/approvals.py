"""Human approval records for proposed consequential actions.

Approval decisions are state records only: they do not execute external tools.
Configure ALEX_APPROVAL_STORE_PATH for restart-safe persistence. Without a path,
this service is intentionally in-memory and suitable only for local development/tests.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import hashlib
from threading import RLock
from uuid import uuid4


class ApprovalError(ValueError):
    """A requested approval transition is invalid."""


class ApprovalStoreError(RuntimeError):
    """Approval state could not safely be loaded or persisted."""


@dataclass
class ApprovalRecord:
    approval_id: str
    mission_id: str
    action: str
    status: str
    requested_by: str
    requester_key_id: str
    requester_role: str
    requested_at: str
    decided_by: str | None = None
    decider_key_id: str | None = None
    decision: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    action_digest: str = ""
    execution_permit_id: str | None = None
    execution_permit_issued_at: str | None = None
    execution_permit_issued_by: str | None = None
    execution_permit_issued_key_id: str | None = None
    execution_permit_consumed_at: str | None = None
    execution_permit_consumed_by: str | None = None
    execution_permit_consumed_key_id: str | None = None


class ApprovalService:
    CURRENT_VERSION = 1
    VALID_DECISIONS = frozenset({"approved", "rejected"})

    def __init__(self, path: str | None = None):
        self.path = path
        self._lock = RLock()
        self._records: dict[str, ApprovalRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or data.get("version") != self.CURRENT_VERSION:
                raise ValueError("Unsupported approval store version")
            records = data.get("records")
            if not isinstance(records, list):
                raise ValueError("Approval store records must be a list")
            loaded: dict[str, ApprovalRecord] = {}
            for raw in records:
                if not isinstance(raw, dict):
                    raise ValueError("Approval record must be an object")
                record = ApprovalRecord(**raw)
                if record.approval_id in loaded or record.status not in {"pending", "approved", "rejected"}:
                    raise ValueError("Approval store contains duplicate IDs or invalid status")
                # Backward-compatible migration for v3.14 records created before action binding.
                expected_digest = self._digest(record.mission_id, record.action)
                if not record.action_digest:
                    record.action_digest = expected_digest
                elif record.action_digest != expected_digest:
                    raise ValueError("Approval action digest does not match its stored action")
                if record.execution_permit_consumed_at and not record.execution_permit_id:
                    raise ValueError("Consumed execution permit has no permit identifier")
                loaded[record.approval_id] = record
            self._records = loaded
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ApprovalStoreError("Approval store could not be safely loaded; refusing to reset approval history") from exc

    def _persist(self, records: dict[str, ApprovalRecord]) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        temporary = self.path + ".tmp"
        payload = {"version": self.CURRENT_VERSION, "records": [asdict(r) for r in records.values()]}
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                # Some platforms/filesystems do not support syncing directories.
                pass
        except OSError as exc:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass
            raise ApprovalStoreError("Approval state could not be durably saved") from exc

    @staticmethod
    def _digest(mission_id: str, action: str) -> str:
        """Stable SHA-256 binding for the exact mission/action pair."""
        canonical = json.dumps(
            {"mission_id": mission_id, "action": action.strip()},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _require_executor(actor: dict) -> tuple[str, str]:
        role = str(actor.get("role", ""))
        if role not in {"admin", "operator"}:
            raise ApprovalError("Only an admin or operator can issue or consume an execution permit")
        actor_id = str(actor.get("actor_id", ""))
        key_id = str(actor.get("key_id", ""))
        if not actor_id or not key_id:
            raise ApprovalError("Authenticated actor and key identifiers are required")
        return actor_id, key_id

    def create(self, mission_id: str, action: str, actor: dict) -> ApprovalRecord:
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise ApprovalError("Mission ID is required")
        if not isinstance(action, str) or not action.strip():
            raise ApprovalError("Action description is required")
        if len(action) > 4000:
            raise ApprovalError("Action description must be 4000 characters or fewer")
        actor_id = str(actor.get("actor_id", "local:owner"))
        key_id = str(actor.get("key_id", "local"))
        role = str(actor.get("role", "admin"))
        record = ApprovalRecord(
            approval_id=str(uuid4()), mission_id=mission_id, action=action.strip(), status="pending",
            requested_by=actor_id, requester_key_id=key_id, requester_role=role,
            requested_at=datetime.now(timezone.utc).isoformat(),
            action_digest=self._digest(mission_id, action.strip()),
        )
        with self._lock:
            updated = dict(self._records)
            updated[record.approval_id] = record
            self._persist(updated)
            self._records = updated
        return record

    def decide(self, approval_id: str, decision: str, actor: dict, reason: str | None = None) -> ApprovalRecord:
        if decision not in self.VALID_DECISIONS:
            raise ApprovalError("Decision must be approved or rejected")
        clean_reason = reason.strip() if isinstance(reason, str) else ""
        if decision == "rejected" and not clean_reason:
            raise ApprovalError("A reason is required when rejecting an action")
        with self._lock:
            current = self._records.get(approval_id)
            if current is None:
                raise ApprovalError("Approval not found")
            if current.status != "pending":
                raise ApprovalError("Only pending approvals can be decided")
            actor_id = str(actor.get("actor_id", ""))
            if actor_id and actor_id == current.requested_by:
                raise ApprovalError("The requester cannot approve or reject their own action")
            if actor.get("role") != "admin":
                raise ApprovalError("Only an admin can approve or reject actions")
            if not actor_id or not actor.get("key_id"):
                raise ApprovalError("Authenticated actor and key identifiers are required")
            updated_record = ApprovalRecord(**asdict(current))
            updated_record.status = decision
            updated_record.decision = decision
            updated_record.decided_by = actor_id or "unknown"
            updated_record.decider_key_id = str(actor.get("key_id", "unknown"))
            updated_record.decision_reason = clean_reason or None
            updated_record.decided_at = datetime.now(timezone.utc).isoformat()
            updated = dict(self._records)
            updated[approval_id] = updated_record
            self._persist(updated)
            self._records = updated
            return updated_record


    def issue_execution_permit(self, approval_id: str, mission_id: str, action: str, actor: dict) -> ApprovalRecord:
        """Issue one single-use permit for an already-approved exact action.

        Issuing a permit does not execute an external action. A future executor must
        consume the permit immediately at its side-effect boundary.
        """
        actor_id, key_id = self._require_executor(actor)
        if not isinstance(mission_id, str) or not mission_id.strip():
            raise ApprovalError("Mission ID is required")
        if not isinstance(action, str) or not action.strip():
            raise ApprovalError("Action description is required")
        digest = self._digest(mission_id, action)
        with self._lock:
            current = self._records.get(approval_id)
            if current is None:
                raise ApprovalError("Approval not found")
            if current.status != "approved":
                raise ApprovalError("Only approved actions can receive an execution permit")
            if current.mission_id != mission_id or current.action_digest != digest:
                raise ApprovalError("Mission or action does not match the approved proposal")
            if current.execution_permit_id is not None:
                raise ApprovalError("An execution permit has already been issued for this approval")
            updated_record = ApprovalRecord(**asdict(current))
            updated_record.execution_permit_id = str(uuid4())
            updated_record.execution_permit_issued_at = datetime.now(timezone.utc).isoformat()
            updated_record.execution_permit_issued_by = actor_id
            updated_record.execution_permit_issued_key_id = key_id
            updated = dict(self._records)
            updated[approval_id] = updated_record
            self._persist(updated)
            self._records = updated
            return updated_record

    def consume_execution_permit(self, permit_id: str, mission_id: str, action: str, actor: dict) -> ApprovalRecord:
        """Atomically consume a permit once, validating its exact action binding.

        This is the guard future connectors must call immediately before a side
        effect. The method only authorizes; it does not perform that side effect.
        """
        actor_id, key_id = self._require_executor(actor)
        if not isinstance(action, str) or not action.strip():
            raise ApprovalError("Action description is required")
        digest = self._digest(mission_id, action)
        with self._lock:
            current = next((r for r in self._records.values() if r.execution_permit_id == permit_id), None)
            if current is None:
                raise ApprovalError("Execution permit not found")
            if current.status != "approved":
                raise ApprovalError("Execution permit is not backed by an approved action")
            if current.mission_id != mission_id or current.action_digest != digest:
                raise ApprovalError("Mission or action does not match the execution permit")
            if current.execution_permit_consumed_at is not None:
                raise ApprovalError("Execution permit has already been consumed")
            updated_record = ApprovalRecord(**asdict(current))
            updated_record.execution_permit_consumed_at = datetime.now(timezone.utc).isoformat()
            updated_record.execution_permit_consumed_by = actor_id
            updated_record.execution_permit_consumed_key_id = key_id
            updated = dict(self._records)
            updated[current.approval_id] = updated_record
            self._persist(updated)
            self._records = updated
            return updated_record

    def get(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                raise ApprovalError("Approval not found")
            return record

    def list(self, status: str | None = None) -> list[ApprovalRecord]:
        if status is not None and status not in {"pending", "approved", "rejected"}:
            raise ApprovalError("Unknown approval status")
        with self._lock:
            records = list(self._records.values())
            if status is not None:
                records = [record for record in records if record.status == status]
            return records
