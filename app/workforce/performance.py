from dataclasses import dataclass, field
from collections import defaultdict
from statistics import mean
from typing import Iterable

@dataclass
class PerformanceEvent:
    agent_id: str
    task_id: str
    tool: str
    status: str
    verified: bool
    budget_spent: float = 0.0
    duration_ms: float = 0.0
    confidence: float = 0.0

@dataclass
class AgentPerformanceProfile:
    agent_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_uncertain: int = 0
    verification_rate: float = 0.0
    success_rate: float = 0.0
    average_confidence: float = 0.0
    average_duration_ms: float = 0.0
    budget_spent: float = 0.0
    tool_stats: dict[str, dict[str, float]] = field(default_factory=dict)

class PerformanceService:
    """Records observable outcomes and ranks already-authorized candidates.

    Performance never grants permissions or changes an agent's trust level.
    """
    def __init__(self):
        self.events: list[PerformanceEvent] = []

    def record(self, event: PerformanceEvent) -> PerformanceEvent:
        self.events.append(event)
        return event

    def profile(self, agent_id: str) -> AgentPerformanceProfile:
        events = [e for e in self.events if e.agent_id == agent_id]
        completed = sum(e.status == "completed" for e in events)
        failed = sum(e.status == "failed" or e.status == "timeout" for e in events)
        uncertain = sum(e.status == "uncertain" for e in events)
        total = len(events)
        verified = sum(e.verified for e in events)
        tool_stats = {}
        for tool in sorted({e.tool for e in events}):
            te = [e for e in events if e.tool == tool]
            tool_stats[tool] = {
                "tasks": float(len(te)),
                "success_rate": sum(e.status == "completed" for e in te) / len(te),
                "verification_rate": sum(e.verified for e in te) / len(te),
                "average_duration_ms": mean(e.duration_ms for e in te),
                "average_confidence": mean(e.confidence for e in te),
            }
        return AgentPerformanceProfile(
            agent_id=agent_id,
            tasks_completed=completed,
            tasks_failed=failed,
            tasks_uncertain=uncertain,
            verification_rate=verified / total if total else 0.0,
            success_rate=completed / total if total else 0.0,
            average_confidence=mean(e.confidence for e in events) if events else 0.0,
            average_duration_ms=mean(e.duration_ms for e in events) if events else 0.0,
            budget_spent=sum(e.budget_spent for e in events),
            tool_stats=tool_stats,
        )

    def recommend(self, agents: Iterable, tool: str) -> list[dict]:
        """Rank only candidates already possessing the requested tool."""
        candidates = [a for a in agents if tool in a.tools]
        scored = []
        for agent in candidates:
            p = self.profile(agent.agent_id)
            ts = p.tool_stats.get(tool)
            if ts:
                score = (0.50 * ts["success_rate"] +
                         0.30 * ts["verification_rate"] +
                         0.20 * ts["average_confidence"])
            else:
                score = 0.0
            scored.append({"agent_id": agent.agent_id, "tool": tool, "score": round(score, 4), "sample_size": int(ts["tasks"]) if ts else 0})
        return sorted(scored, key=lambda x: (-x["score"], x["agent_id"]))
