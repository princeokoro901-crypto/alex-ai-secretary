from uuid import uuid4
from app.workforce.models import Handoff

class HandoffService:
    def __init__(self, workforce, missions):
        self.workforce = workforce
        self.missions = missions
        self.handoffs = {}

    def create(self, mission_id, from_agent, to_agent, task_id, findings=None, evidence=None, unresolved_questions=None, verification=None):
        mission = self.missions.get(mission_id)
        if from_agent not in mission.assigned_agents or to_agent not in mission.assigned_agents:
            raise ValueError("Both agents must be assigned to the mission")
        if from_agent == to_agent:
            raise ValueError("Handoff requires different agents")
        recipient = self.workforce.agents.get(to_agent)
        if recipient is None or getattr(recipient.status, "value", recipient.status) not in ("ready", "running"):
            raise ValueError("Recipient agent is not available")
        h = Handoff(
            handoff_id=str(uuid4()), from_agent=from_agent, to_agent=to_agent,
            task_id=task_id, findings=findings or {}, evidence=evidence or [],
            unresolved_questions=unresolved_questions or [], verification=verification or {}
        )
        self.handoffs[h.handoff_id] = h
        return h
