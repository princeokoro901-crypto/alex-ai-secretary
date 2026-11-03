from uuid import uuid4
from app.agents.models import AgentTask, AgentResult
from app.missions.models import MissionStatus
from app.workforce.models import AgentStatus
from app.verification.service import VerificationService
from app.recovery.service import RecoveryService, RecoveryAction
from app.agents.memory import ScopedMemoryService
from app.workforce.performance import PerformanceService, PerformanceEvent

class AgentRuntime:
    """Executes only tasks that satisfy workforce + mission boundaries."""
    def __init__(self, workforce, missions):
        self.workforce = workforce
        self.missions = missions
        self.tasks = {}
        self.results = {}
        self.idempotency = {}
        self.verifier = VerificationService()
        self.recovery = RecoveryService()
        self.memory = ScopedMemoryService()
        self.performance = PerformanceService()

    def remember(self, agent_id, key, value, scope="assigned_task_context", sensitivity="normal", importance=0.5):
        agent = self.workforce.agents.get(agent_id)
        if agent is None:
            raise ValueError("Agent is not registered in the workforce")
        if scope not in agent.memory_scope:
            raise ValueError("Agent cannot write outside its memory scope")
        return self.memory.remember(agent_id, key, value, scope, sensitivity, importance)

    def submit(self, mission_id, agent_id, objective, tool, input_data=None, budget=0.0, idempotency_key=None):
        if idempotency_key and idempotency_key in self.idempotency:
            return self.results[self.idempotency[idempotency_key]]
        mission = self.missions.get(mission_id)
        if mission.status != MissionStatus.RUNNING:
            raise ValueError("Mission must be running before agent execution")
        if agent_id not in mission.assigned_agents:
            raise ValueError("Agent is not assigned to this mission")
        agent = self.workforce.agents.get(agent_id)
        if agent is None:
            raise ValueError("Agent is not registered in the workforce")
        if agent.status.value not in {"ready", "running"}:
            raise ValueError("Agent is not available for execution")
        if tool not in agent.tools:
            raise ValueError("Agent is not authorized to use this tool")
        if mission.contract.allowed_tools and tool not in mission.contract.allowed_tools:
            raise ValueError("Mission does not allow this tool")
        if budget < 0:
            raise ValueError("Task budget cannot be negative")
        if mission.spent_budget + budget > mission.contract.budget:
            mission.status = MissionStatus.STOPPED
            raise ValueError("Mission budget exceeded")
        task_input = input_data or {}
        allowed_scopes = list(agent.memory_scope)
        requested_scopes = task_input.get("memory_scopes", allowed_scopes)
        if any(scope not in allowed_scopes for scope in requested_scopes):
            raise ValueError("Agent requested memory outside its authorized scope")
        max_sensitivity = task_input.get("max_memory_sensitivity", "normal")
        context = self.memory.context_dicts(agent_id, requested_scopes, max_sensitivity=max_sensitivity)
        outcome = task_input.get("simulated_outcome", "success")
        if outcome not in {"success", "failed", "timeout", "uncertain"}:
            raise ValueError("Unsupported execution outcome")
        task = AgentTask(str(uuid4()), mission_id, agent_id, objective, tool, input_data or {}, budget, idempotency_key)
        self.tasks[task.task_id] = task
        mission.spent_budget += budget
        agent.status = AgentStatus.RUNNING
        evidence = self.verifier.add_evidence(
            source=f"agent:{agent_id}",
            claim=f"Agent executed permitted tool {tool} for the requested objective",
            verified=True, method="runtime_boundary_check",
        )
        verification = self.verifier.verify(
            task.task_id,
            checks=[
                {"name": "agent_registered", "passed": True},
                {"name": "agent_assigned", "passed": True},
                {"name": "tool_authorized", "passed": True},
                {"name": "mission_tool_allowed", "passed": True},
            ],
            evidence_ids=[evidence.evidence_id],
        )
        if outcome == "success":
            result_status = "completed" if verification.verified else "uncertain"
        else:
            result_status = outcome
        result = AgentResult(
            task_id=task.task_id, mission_id=mission_id, agent_id=agent_id,
            status=result_status,
            findings={"objective": objective, "tool": tool},
            evidence=[{"evidence_id": evidence.evidence_id, "source": evidence.source, "claim": evidence.claim}],
            verification={"verified": verification.verified, "verification_id": verification.verification_id, "status": verification.status},
            provenance={"runtime": "alex-agent-runtime", "agent_id": agent_id, "tool": tool, "memory_scopes": requested_scopes},
            spent_budget=budget, context=context,
        )
        self.results[task.task_id] = result
        confidence = 1.0 if result_status == "completed" else (0.5 if result_status == "uncertain" else 0.0)
        self.performance.record(PerformanceEvent(
            agent_id=agent_id, task_id=task.task_id, tool=tool,
            status=result_status, verified=bool(verification.verified),
            budget_spent=budget, duration_ms=float(task_input.get("simulated_duration_ms", 0)),
            confidence=confidence,
        ))
        if idempotency_key:
            self.idempotency[idempotency_key] = task.task_id
        if result_status in {"failed", "timeout", "uncertain"}:
            recovery = self.recovery.decide(task.task_id, result_status, reason=f"Agent execution returned {result_status}", recovery_key=(input_data or {}).get("recovery_key", objective))
            mission.results.append({"type": "recovery", "task_id": task.task_id, "action": recovery.action.value, "attempt": recovery.attempt})
            if recovery.action == RecoveryAction.ESCALATE:
                mission.status = MissionStatus.PAUSED
        agent.status = AgentStatus.READY
        mission.results.append({"type": "agent_result", "task_id": task.task_id, "agent_id": agent_id, "verified": result_status == "completed"})
        return result
