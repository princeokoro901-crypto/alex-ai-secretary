import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from app.missions.models import MissionContract, MissionStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OrchestrationRequest:
    objective: str
    task_type: str
    required_tools: list[str]
    tool: str
    budget: float = 0.0
    input: dict = field(default_factory=dict)
    mission_id: str | None = None
    candidate_agent_ids: list[str] | None = None
    verification_required: bool = True
    idempotency_key: str | None = None
    initiated_by: str = "system"
    initiated_by_key_id: str = "system"
    initiated_by_role: str = "system"

@dataclass
class OrchestrationResult:
    orchestration_id: str
    mission_id: str
    status: str
    selected_agent_id: str
    result: dict
    route_reason: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    recovery_state: str = "none"
    request_metadata: dict = field(default_factory=dict)
    recovery_review: dict | None = None

class RecoveryReviewAuditError(RuntimeError):
    """A review was persisted, but its audit event could not be written."""

class OrchestrationStateStore:
    """Durable JSON store with schema versioning and corruption recovery."""
    CURRENT_VERSION = 3
    def __init__(self, path: str | None = None):
        self.path = path
        self._lock = RLock()

    def load(self) -> tuple[dict[str, OrchestrationResult], dict[str, str]]:
        if not self.path or not os.path.exists(self.path):
            return {}, {}
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data = self._migrate(data)
                runs = {oid: OrchestrationResult(**payload) for oid, payload in data.get("runs", {}).items()}
                return runs, dict(data.get("idempotency", {}))
            except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                self._quarantine_corrupt(exc)
                return {}, {}

    def _migrate(self, data: dict) -> dict:
        version = int(data.get("version", 1))
        if version == self.CURRENT_VERSION:
            return data
        if version in {1, 2}:
            # The recovery-review field is optional on older records, so this is
            # a backwards-compatible metadata migration with no data rewrite needed.
            data["version"] = self.CURRENT_VERSION
            return data
        raise ValueError(f"Unsupported orchestration state version: {version}")

    def _quarantine_corrupt(self, exc: Exception) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        quarantine = self.path + ".corrupt"
        try:
            os.replace(self.path, quarantine)
        except OSError:
            pass

    def save(self, runs: dict[str, OrchestrationResult], idempotency: dict[str, str]) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        payload = {
            "version": self.CURRENT_VERSION,
            "runs": {oid: asdict(result) for oid, result in runs.items()},
            "idempotency": idempotency,
        }
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)

class SQLiteOrchestrationStateStore:
    """Transactional SQLite store implementing the orchestration state-store contract."""
    CURRENT_VERSION = 2

    def __init__(self, path: str):
        self.path = path
        self._lock = RLock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS runs (orchestration_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, orchestration_id TEXT NOT NULL)")
            version_row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            if version_row is None:
                conn.execute("INSERT INTO meta(key, value) VALUES ('version', ?)", (str(self.CURRENT_VERSION),))
            else:
                existing_version = int(version_row[0])
                if existing_version == 1:
                    # Stored run payloads are JSON; the new optional field has a
                    # dataclass default, so version 1 can be safely migrated.
                    conn.execute("UPDATE meta SET value=? WHERE key='version'", (str(self.CURRENT_VERSION),))
                elif existing_version != self.CURRENT_VERSION:
                    raise ValueError(f"Unsupported SQLite orchestration state version: {existing_version}")

    def load(self) -> tuple[dict[str, OrchestrationResult], dict[str, str]]:
        with self._lock, self._connect() as conn:
            version = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            if version is None or int(version[0]) != self.CURRENT_VERSION:
                raise ValueError("Unsupported SQLite orchestration state version")
            runs = {}
            for oid, payload in conn.execute("SELECT orchestration_id, payload FROM runs"):
                runs[oid] = OrchestrationResult(**json.loads(payload))
            idem = {k: oid for k, oid in conn.execute("SELECT idempotency_key, orchestration_id FROM idempotency")}
            return runs, idem

    def save(self, runs: dict[str, OrchestrationResult], idempotency: dict[str, str]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM runs")
            conn.execute("DELETE FROM idempotency")
            conn.executemany("INSERT INTO runs(orchestration_id, payload) VALUES (?, ?)", [(oid, json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)) for oid, result in runs.items()])
            conn.executemany("INSERT INTO idempotency(idempotency_key, orchestration_id) VALUES (?, ?)", list(idempotency.items()))
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', ?)", (str(self.CURRENT_VERSION),))
            conn.commit()


class OrchestratorService:
    """End-to-end coordinator. Selection never grants authority; runtime re-checks every boundary."""
    def __init__(self, workforce, missions, router, runtime, state_store: OrchestrationStateStore | None = None, audit_log=None):
        self.workforce = workforce
        self.missions = missions
        self.router = router
        self.runtime = runtime
        self.state_store = state_store or OrchestrationStateStore()
        self.runs, self.idempotency = self.state_store.load()
        self.audit_log = audit_log
        self._review_lock = RLock()
        self._recover_interrupted_runs()

    def _persist(self) -> None:
        self.state_store.save(self.runs, self.idempotency)

    def _recover_interrupted_runs(self) -> None:
        """Conservatively mark durable in-flight runs uncertain after process restart.

        This never re-executes work. The pre-execution intent record and idempotency
        mapping are durable before the runtime is called, so a replay with the same
        key returns this uncertain record rather than starting a second task.
        """
        recovered = []
        now = _utc_now()
        for orchestration_id, record in self.runs.items():
            if record.status != "running":
                continue
            record.status = "uncertain"
            record.updated_at = now
            record.recovery_state = "interrupted_after_restart"
            record.result = {
                "recovery": {
                    "state": "interrupted_after_restart",
                    "automatic_retry": False,
                    "message": "Alex restarted while this task was in progress. Its outcome cannot be confirmed; inspect it before deciding on any further action.",
                },
                "request_metadata": record.request_metadata,
            }
            recovered.append(orchestration_id)
        if recovered:
            self._persist()
            if self.audit_log:
                for orchestration_id in recovered:
                    record = self.runs[orchestration_id]
                    self.audit_log.record(
                        "orchestration_interrupted", "system", "orchestration", orchestration_id,
                        {"status": "uncertain", "recovery_state": record.recovery_state,
                         "mission_id": record.mission_id, "automatic_retry": False},
                    )

    @staticmethod
    def _request_metadata(request: OrchestrationRequest, mission_id: str) -> dict:
        """Store useful diagnostic metadata without persisting raw task input."""
        input_bytes = json.dumps(
            request.input or {}, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        ).encode("utf-8")
        return {
            "objective": request.objective,
            "task_type": request.task_type,
            "required_tools": list(request.required_tools),
            "tool": request.tool,
            "budget": request.budget,
            "mission_id": mission_id,
            "candidate_agent_ids": list(request.candidate_agent_ids or []),
            "verification_required": request.verification_required,
            "input_keys": sorted(str(key) for key in (request.input or {}).keys()),
            "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
        }

    def record_recovery_review(self, orchestration_id: str, disposition: str, actor: dict) -> OrchestrationResult:
        """Record one immutable human disposition for an uncertain orchestration.

        This records an acknowledgement/decision only. It does not change task status,
        retry work, or assert that an external operation did or did not happen.
        """
        allowed = {"acknowledged_no_retry", "escalated_for_manual_investigation"}
        if disposition not in allowed:
            raise ValueError("Unsupported recovery disposition")
        with self._review_lock:
            record = self.runs.get(orchestration_id)
            if record is None:
                raise KeyError("Orchestration not found")
            if record.status != "uncertain":
                raise ValueError("Only uncertain orchestrations can receive a recovery review")
            if record.recovery_review is not None:
                raise ValueError("A recovery review has already been recorded and is immutable")

            review = {
                "disposition": disposition,
                "reviewed_at": _utc_now(),
                "reviewed_by": str(actor.get("actor_id", "unknown")),
                "reviewer_key_id": str(actor.get("key_id", "unknown")),
                "reviewer_role": str(actor.get("role", "unknown")),
            }
            record.recovery_review = review
            try:
                self._persist()
            except Exception:
                # A failed state-store write must not leave a phantom review in memory.
                record.recovery_review = None
                raise
            if self.audit_log:
                try:
                    self.audit_log.record(
                        "orchestration_recovery_reviewed",
                        review["reviewed_by"],
                        "orchestration",
                        orchestration_id,
                        {
                            "actor_id": review["reviewed_by"],
                            "key_id": review["reviewer_key_id"],
                            "role": review["reviewer_role"],
                            "disposition": disposition,
                            "status_remains": record.status,
                            "recovery_state": record.recovery_state,
                            "automatic_retry": False,
                        },
                    )
                except Exception as exc:
                    # The immutable review is already persisted. Do not roll it back
                    # or invite a duplicate decision; tell the caller to inspect it.
                    raise RecoveryReviewAuditError(
                        "Review was saved, but its audit event could not be written. Inspect the run before taking further action."
                    ) from exc
            return record

    def consistency_report(self, audit_events=None) -> dict:
        """Check durable orchestration state against audit lifecycle events.

        This is a diagnostic consistency check; it does not mutate either store.
        """
        audits = list(audit_events or [])
        by_id = {}
        for event in audits:
            if event.resource_type == "orchestration":
                by_id.setdefault(event.resource_id, []).append(event)

        missing_audit = []
        duplicate_completion = []
        replay_without_run = []
        for oid in self.runs:
            events = by_id.get(oid, [])
            completions = [e for e in events if e.event_type == "orchestration_completed"]
            terminal_events = [e for e in events if e.event_type in {"orchestration_completed", "orchestration_interrupted"}]
            if not terminal_events:
                missing_audit.append(oid)
            if len(completions) > 1:
                duplicate_completion.append(oid)
        for oid, events in by_id.items():
            if oid not in self.runs and any(e.event_type in {"orchestration_completed", "orchestration_interrupted", "orchestration_replayed"} for e in events):
                replay_without_run.append(oid)

        issues = []
        issues.extend({"type": "missing_audit", "orchestration_id": oid} for oid in missing_audit)
        issues.extend({"type": "duplicate_completion", "orchestration_id": oid} for oid in duplicate_completion)
        issues.extend({"type": "audit_without_run", "orchestration_id": oid} for oid in replay_without_run)
        return {
            "consistent": not issues,
            "orchestration_count": len(self.runs),
            "audit_orchestration_count": len(by_id),
            "issues": issues,
        }

    def run(self, request: OrchestrationRequest) -> OrchestrationResult:
        if request.idempotency_key and request.idempotency_key in self.idempotency:
            cached_id = self.idempotency[request.idempotency_key]
            cached = self.runs.get(cached_id)
            if cached is None:
                raise ValueError("Idempotency key points to a missing orchestration record; no new task was started")
            if cached.request_metadata:
                expected_mission_id = request.mission_id or cached.mission_id
                requested_metadata = self._request_metadata(request, expected_mission_id)
                if requested_metadata != cached.request_metadata:
                    raise ValueError("Idempotency key was already used for a different request")
            if self.audit_log:
                self.audit_log.record("orchestration_replayed", request.initiated_by, "orchestration", cached.orchestration_id, {"idempotency_key": request.idempotency_key, "actor_id": request.initiated_by, "key_id": request.initiated_by_key_id, "role": request.initiated_by_role, "cached_status": cached.status})
            return cached
        if not request.objective.strip():
            raise ValueError("Objective is required")
        if request.budget < 0:
            raise ValueError("Budget cannot be negative")
        if not isinstance(request.input, dict):
            raise ValueError("Task input must be an object")
        if not request.required_tools:
            raise ValueError("At least one required tool is required")
        if request.tool not in request.required_tools:
            raise ValueError("Execution tool must be one of the required tools")

        mission_id = request.mission_id or str(uuid4())
        orchestration_id = str(uuid4())
        if request.mission_id:
            mission = self.missions.get(mission_id)
            if mission.status != MissionStatus.RUNNING:
                raise ValueError("Provided mission must already be running")
        else:
            contract = MissionContract(
                mission_id=mission_id,
                objective=request.objective,
                allowed_tools=list(dict.fromkeys(request.required_tools)),
                budget=request.budget,
                verification_required=request.verification_required,
                success_conditions=["verified_agent_result"],
            )
            mission = self.missions.create(contract)

        if self.audit_log:
            self.audit_log.record("orchestration_started", request.initiated_by, "orchestration", orchestration_id, {
                "actor_id": request.initiated_by, "key_id": request.initiated_by_key_id, "role": request.initiated_by_role,
                "mission_id": mission_id,
                "idempotency_key": request.idempotency_key,
                "task_type": request.task_type,
            })

        candidates = self.router.route(
            request.task_type,
            request.required_tools,
            mission_id=mission_id if request.mission_id else None,
            candidate_agent_ids=request.candidate_agent_ids,
        )
        if not candidates:
            raise ValueError("No authorized agent is available for this work")

        selected = candidates[0]
        if selected.agent_id not in mission.assigned_agents:
            self.missions.assign_agent(mission_id, selected.agent_id)
        if mission.status != MissionStatus.RUNNING:
            self.missions.start(mission_id)

        # Persist the intent and idempotency mapping BEFORE calling the runtime.
        # A restart from this point onward is conservatively recovered as uncertain.
        now = _utc_now()
        output = OrchestrationResult(
            orchestration_id=orchestration_id,
            mission_id=mission_id,
            status="running",
            selected_agent_id=selected.agent_id,
            result={"message": "Execution has been recorded as in progress; no final result is available yet."},
            route_reason=selected.reasons,
            created_at=now,
            updated_at=now,
            recovery_state="execution_pending",
            request_metadata=self._request_metadata(request, mission_id),
        )
        self.runs[orchestration_id] = output
        if request.idempotency_key:
            self.idempotency[request.idempotency_key] = orchestration_id
        self._persist()

        try:
            result = self.runtime.submit(
                mission_id=mission_id,
                agent_id=selected.agent_id,
                objective=request.objective,
                tool=request.tool,
                input_data=request.input,
                budget=request.budget,
                idempotency_key=(request.input or {}).get("idempotency_key"),
            )
        except Exception as exc:
            # Once runtime entry has been attempted, an exception cannot prove that
            # no work occurred. Record uncertainty; never auto-retry this operation.
            if mission.status == MissionStatus.RUNNING:
                mission.status = MissionStatus.PAUSED
            output.status = "uncertain"
            output.updated_at = _utc_now()
            output.recovery_state = "runtime_exception"
            output.result = {
                "recovery": {
                    "state": "runtime_exception",
                    "automatic_retry": False,
                    "error_type": type(exc).__name__,
                    "message": "The runtime did not return a reliable result. The task outcome may be uncertain; inspect before deciding on any further action.",
                },
                "request_metadata": output.request_metadata,
            }
            self._persist()
            if self.audit_log:
                self.audit_log.record("orchestration_interrupted", request.initiated_by, "orchestration", orchestration_id, {
                    "actor_id": request.initiated_by, "key_id": request.initiated_by_key_id,
                    "role": request.initiated_by_role, "mission_id": mission_id,
                    "status": "uncertain", "recovery_state": output.recovery_state,
                    "error_type": type(exc).__name__, "automatic_retry": False,
                })
            return output

        status = result.status
        if status == "completed":
            self.missions.complete(mission_id, {"type": "orchestration", "agent_id": selected.agent_id, "verified": True})
            mission.results[-1]["orchestration_id"] = orchestration_id
        elif mission.status == MissionStatus.RUNNING:
            mission.status = MissionStatus.PAUSED

        output.status = status
        output.result = result.__dict__
        output.updated_at = _utc_now()
        if status == "uncertain":
            output.recovery_state = "runtime_reported_uncertain"
        elif status in {"failed", "timeout"}:
            output.recovery_state = "runtime_reported_failure"
        else:
            output.recovery_state = "none"
        self._persist()
        if self.audit_log:
            self.audit_log.record("orchestration_completed", request.initiated_by, "orchestration", orchestration_id, {"actor_id": request.initiated_by, "key_id": request.initiated_by_key_id, "role": request.initiated_by_role, "mission_id": mission_id, "agent_id": selected.agent_id, "status": status, "verified": status == "completed", "recovery_state": output.recovery_state})
        return output
