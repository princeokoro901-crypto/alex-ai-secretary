from dataclasses import dataclass

@dataclass
class RouteCandidate:
    agent_id: str
    task_type: str
    score: float
    sample_size: int
    reasons: list[str]

class AgentRoutingService:
    """Selects agents only from the set already authorized for the requested work."""
    def __init__(self, workforce, missions, performance):
        self.workforce = workforce
        self.missions = missions
        self.performance = performance

    def route(self, task_type: str, required_tools: list[str], mission_id: str | None = None,
              candidate_agent_ids: list[str] | None = None) -> list[RouteCandidate]:
        if not task_type.strip():
            raise ValueError("Task type is required")
        required_tools = list(dict.fromkeys(required_tools))
        if not required_tools:
            raise ValueError("At least one required tool is required")

        if mission_id is not None:
            mission = self.missions.get(mission_id)
            assigned = set(mission.assigned_agents)
        else:
            assigned = None

        ids = candidate_agent_ids if candidate_agent_ids is not None else list(self.workforce.agents)
        unknown = [aid for aid in ids if aid not in self.workforce.agents]
        if unknown:
            raise ValueError(f"Unknown candidate agent: {unknown[0]}")

        results = []
        for aid in ids:
            agent = self.workforce.agents[aid]
            if assigned is not None and aid not in assigned:
                continue
            if agent.status.value not in {"ready", "running"}:
                continue
            if any(tool not in agent.tools for tool in required_tools):
                continue
            if mission_id is not None:
                mission = self.missions.get(mission_id)
                if mission.contract.allowed_tools and any(tool not in mission.contract.allowed_tools for tool in required_tools):
                    continue

            per_tool = [self.performance.profile(aid).tool_stats.get(tool) for tool in required_tools]
            samples = sum(int(s["tasks"]) for s in per_tool if s)
            if per_tool and all(s for s in per_tool):
                score = sum(0.5*s["success_rate"] + 0.3*s["verification_rate"] + 0.2*s["average_confidence"] for s in per_tool) / len(per_tool)
            else:
                score = 0.0
            reasons = ["registered", "available", "required_tools_authorized"]
            if mission_id is not None:
                reasons.append("mission_assigned")
            reasons.append("performance_advisory")
            results.append(RouteCandidate(aid, task_type, round(score, 4), samples, reasons))

        return sorted(results, key=lambda x: (-x.score, -x.sample_size, x.agent_id))
