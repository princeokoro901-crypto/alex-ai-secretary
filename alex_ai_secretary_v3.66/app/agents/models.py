from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentTask:
    task_id: str
    mission_id: str
    agent_id: str
    objective: str
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    budget: float = 0.0
    idempotency_key: str | None = None

@dataclass
class AgentResult:
    task_id: str
    mission_id: str
    agent_id: str
    status: str
    findings: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    actions_proposed: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    spent_budget: float = 0.0
    context: list[dict[str, Any]] = field(default_factory=list)
