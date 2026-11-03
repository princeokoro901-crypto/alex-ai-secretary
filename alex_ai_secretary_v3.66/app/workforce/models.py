from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from typing import Any

def now():
    return datetime.now(timezone.utc)

class AgentStatus(str, Enum):
    READY="ready"; RUNNING="running"; PAUSED="paused"; FAILED="failed"; RETIRED="retired"

class TrustLevel(int, Enum):
    INFORMATIONAL=0
    LOW_RISK=1
    APPROVAL_REQUIRED=2
    HIGHLY_SENSITIVE=3
    HUMAN_ONLY=4

class Agent(BaseModel):
    agent_id: str
    name: str
    role: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    memory_scope: list[str] = Field(default_factory=list)
    trust_level: TrustLevel = TrustLevel.INFORMATIONAL
    status: AgentStatus = AgentStatus.READY

class Team(BaseModel):
    team_id: str
    name: str
    objective: str
    owner: str
    agent_ids: list[str] = Field(default_factory=list)
    budget_limit: float = 0.0
    budget_used: float = 0.0
    status: str = "active"

class Delegation(BaseModel):
    delegation_id: str
    team_id: str
    objective: str
    agent_id: str | None = None
    deadline: datetime | None = None
    budget: float = 0.0
    requires_approval: bool = False
    status: str = "delegated"
    created_at: datetime = Field(default_factory=now)

class Handoff(BaseModel):
    handoff_id: str
    from_agent: str
    to_agent: str
    task_id: str
    findings: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)

class PerformanceRecord(BaseModel):
    agent_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    average_confidence: float = 0.0
    budget_spent: float = 0.0
    verification_rate: float = 0.0
