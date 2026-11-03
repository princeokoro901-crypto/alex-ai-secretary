from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

class MissionStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"

@dataclass
class MissionContract:
    mission_id: str
    objective: str
    scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    allowed_data: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    budget: float = 0.0
    deadline: str | None = None
    approvals: list[str] = field(default_factory=list)
    verification_required: bool = True
    success_conditions: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)

@dataclass
class Mission:
    contract: MissionContract
    status: MissionStatus = MissionStatus.DRAFT
    assigned_agents: list[str] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    spent_budget: float = 0.0
    results: list[dict[str, Any]] = field(default_factory=list)
    recovery_decisions: list[dict[str, Any]] = field(default_factory=list)

class MissionService:
    """Mission lifecycle service with optional SQLite durability.

    Set ALEX_MISSION_STORE_PATH to enable durable state. On restart, missions
    found in RUNNING are conservatively paused with a recovery checkpoint; they
    are never resumed or re-executed automatically.
    """
    def __init__(self, workforce=None, store_path: str | None = None):
        self._missions: dict[str, Mission] = {}
        self.workforce = workforce
        self._store_path = store_path or os.getenv("ALEX_MISSION_STORE_PATH")
        self._db = None
        if self._store_path:
            path = Path(self._store_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS missions (mission_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            self._db.commit()
            self._load()

    def _mission_to_dict(self, mission: Mission) -> dict[str, Any]:
        data = asdict(mission)
        data["status"] = mission.status.value
        return data

    def _dict_to_mission(self, data: dict[str, Any]) -> Mission:
        contract_data = dict(data["contract"])
        contract = MissionContract(**contract_data)
        return Mission(contract=contract, status=MissionStatus(data.get("status", "draft")),
                       assigned_agents=list(data.get("assigned_agents", [])),
                       checkpoints=list(data.get("checkpoints", [])),
                       spent_budget=float(data.get("spent_budget", 0.0)),
                       results=list(data.get("results", [])),
                       recovery_decisions=list(data.get("recovery_decisions", [])))

    def _persist(self, mission: Mission) -> None:
        if self._db is None:
            return
        payload = json.dumps(self._mission_to_dict(mission), sort_keys=True, separators=(",", ":"))
        with self._db:
            self._db.execute("INSERT INTO missions(mission_id,payload) VALUES(?,?) ON CONFLICT(mission_id) DO UPDATE SET payload=excluded.payload",
                              (mission.contract.mission_id, payload))

    def _load(self) -> None:
        rows = self._db.execute("SELECT payload FROM missions ORDER BY mission_id").fetchall()
        for (payload,) in rows:
            mission = self._dict_to_mission(json.loads(payload))
            if mission.status == MissionStatus.RUNNING:
                mission.status = MissionStatus.PAUSED
                mission.checkpoints.append({"name": "recovered_after_restart_manual_review_required", "verified": False})
                self._persist(mission)
            self._missions[mission.contract.mission_id] = mission

    def record_recovery_decision(self, mission_id: str, decision: str, actor_id: str, reason: str, key_id: str | None = None, role: str | None = None) -> Mission:
        """Record a human recovery decision for a paused mission without resuming work.

        Supported decisions are ``keep_paused`` and ``stop``. This service method
        records attribution but does not itself authenticate the actor; API callers
        must enforce authorization before invoking it.
        """
        mission = self.get(mission_id)
        if mission.status != MissionStatus.PAUSED:
            raise ValueError("Recovery decisions require a paused mission")
        if not any(c.get("name") == "recovered_after_restart_manual_review_required" for c in mission.checkpoints):
            raise ValueError("Mission is not awaiting restart recovery review")
        if mission.recovery_decisions:
            raise ValueError("Recovery decision already recorded")
        if decision not in {"keep_paused", "stop"}:
            raise ValueError("Unsupported recovery decision")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("Recovery decision actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Recovery decision reason is required")
        from datetime import datetime, timezone
        record = {"decision_id": str(uuid4()), "decision": decision, "actor_id": actor_id.strip(),
                  "reason": reason.strip(), "key_id": key_id, "role": role, "audit_status": "pending",
                  "recorded_at": datetime.now(timezone.utc).isoformat()}
        old_status = mission.status
        mission.recovery_decisions.append(record)
        if decision == "stop":
            mission.status = MissionStatus.STOPPED
        try:
            self._persist(mission)
        except Exception:
            mission.recovery_decisions.pop()
            mission.status = old_status
            raise
        return mission

    def mark_recovery_audit_status(self, mission_id: str, decision_id: str, status: str) -> Mission:
        """Persist audit-delivery state for a recorded recovery decision."""
        if status not in {"pending", "recorded"}:
            raise ValueError("Unsupported audit status")
        mission = self.get(mission_id)
        record = next((item for item in mission.recovery_decisions if item.get("decision_id") == decision_id), None)
        if record is None:
            raise ValueError("Recovery decision not found")
        previous = record.get("audit_status", "pending")
        if previous == status:
            return mission
        record["audit_status"] = status
        try:
            self._persist(mission)
        except Exception:
            record["audit_status"] = previous
            raise
        return mission

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def create(self, contract: MissionContract) -> Mission:
        if not contract.objective.strip():
            raise ValueError("Mission objective is required")
        if contract.budget < 0:
            raise ValueError("Mission budget cannot be negative")
        if contract.mission_id in self._missions:
            raise ValueError("Mission ID already exists")
        if not contract.stop_conditions:
            contract.stop_conditions = ["stop_on_forbidden_action", "stop_on_budget_exceeded"]
        mission = Mission(contract)
        self._missions[contract.mission_id] = mission
        self._persist(mission)
        return mission

    def list(self) -> list[Mission]:
        return list(self._missions.values())

    def get(self, mission_id: str) -> Mission:
        if mission_id not in self._missions:
            raise ValueError("Mission not found")
        return self._missions[mission_id]

    def assign_agent(self, mission_id: str, agent_id: str) -> Mission:
        mission = self.get(mission_id)
        if self.workforce is not None and agent_id not in self.workforce.agents:
            raise ValueError("Agent is not registered in the workforce")
        if agent_id not in mission.assigned_agents:
            mission.assigned_agents.append(agent_id)
            self._persist(mission)
        return mission

    def delegate_to_agent(self, mission_id: str, agent_id: str, budget: float = 0.0, requires_approval: bool = False):
        mission = self.get(mission_id)
        self.assign_agent(mission_id, agent_id)
        if self.workforce is None:
            return None
        return self.workforce.delegate({"team_id": mission.contract.mission_id, "agent_id": agent_id,
            "objective": mission.contract.objective, "budget": budget, "requires_approval": requires_approval})

    def start(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        if not mission.assigned_agents:
            raise ValueError("Mission requires at least one assigned agent")
        if mission.status in {MissionStatus.STOPPED, MissionStatus.COMPLETED, MissionStatus.FAILED}:
            raise ValueError(f"Mission cannot start from status: {mission.status.value}")
        if mission.status == MissionStatus.PAUSED and any(
            c.get("name") == "recovered_after_restart_manual_review_required" for c in mission.checkpoints
        ):
            raise ValueError("Restart-recovered missions cannot resume through start(); use a separately authorized recovery workflow")
        mission.status = MissionStatus.RUNNING
        self._persist(mission)
        return mission

    def record_spend(self, mission_id: str, amount: float) -> Mission:
        mission = self.get(mission_id)
        if amount < 0:
            raise ValueError("Spend amount cannot be negative")
        if mission.spent_budget + amount > mission.contract.budget:
            mission.status = MissionStatus.STOPPED
            self._persist(mission)
            raise ValueError("Mission budget exceeded")
        mission.spent_budget += amount
        self._persist(mission)
        return mission

    def validate_action(self, mission_id: str, action: str) -> Mission:
        mission = self.get(mission_id)
        if not isinstance(action, str) or not action.strip():
            raise ValueError("Action description is required")
        for forbidden in mission.contract.forbidden_actions:
            if forbidden.lower() in action.lower():
                mission.status = MissionStatus.STOPPED
                self._persist(mission)
                raise ValueError(f"Mission forbids action: {forbidden}")
        return mission

    def propose_action(self, mission_id: str, action: str) -> Mission:
        mission = self.validate_action(mission_id, action)
        mission.results.append({"type": "action_proposed", "action": action})
        self._persist(mission)
        return mission

    def checkpoint(self, mission_id: str, name: str, verified: bool = False) -> Mission:
        mission = self.get(mission_id)
        mission.checkpoints.append({"name": name, "verified": verified})
        self._persist(mission)
        return mission

    def complete(self, mission_id: str, result: dict[str, Any]) -> Mission:
        mission = self.get(mission_id)
        if mission.contract.verification_required and not result.get("verified", False):
            raise ValueError("Mission requires verified completion")
        mission.results.append(result)
        mission.status = MissionStatus.COMPLETED
        self._persist(mission)
        return mission
