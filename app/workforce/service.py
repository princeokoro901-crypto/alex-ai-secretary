from uuid import uuid4
from app.workforce.models import Agent, Team, Delegation, AgentStatus

class WorkforceService:
    def __init__(self):
        self.agents = {}
        self.teams = {}
        self.delegations = {}
        self._seed_agents()

    def _seed_agents(self):
        agents = [
            ("research", "Research Agent", "research", ["search_web","extract_sources","summarize","compare"]),
            ("content", "Content Agent", "content", ["write","rewrite","plan_content"]),
            ("calendar", "Calendar Agent", "calendar", ["read_calendar","create_event","update_event"]),
            ("communication", "Communication Agent", "communication", ["draft_email","draft_message"]),
            ("analytics", "Analytics Agent", "analytics", ["analyze","report","visualize"]),
        ]
        for aid, name, role, caps in agents:
            self.agents[aid] = Agent(
                agent_id=aid, name=name, role=role,
                capabilities=caps, tools=caps, memory_scope=["assigned_task_context"]
            )

    def list_teams(self):
        return list(self.teams.values())

    def create_team(self, payload):
        team = Team(
            team_id=payload.get("team_id", str(uuid4())),
            name=payload["name"],
            objective=payload["objective"],
            owner=payload.get("owner", "user"),
            agent_ids=payload.get("agent_ids", []),
            budget_limit=float(payload.get("budget_limit", 0))
        )
        self.teams[team.team_id] = team
        return team

    def delegate(self, payload):
        team = self.teams.get(payload["team_id"])
        if team is None:
            raise ValueError("Unknown team")
        aid = payload.get("agent_id")
        if aid and aid not in team.agent_ids:
            raise ValueError("Agent is not a member of the team")
        budget = float(payload.get("budget", 0))
        if team.budget_limit and team.budget_used + budget > team.budget_limit:
            raise ValueError("Delegation exceeds team budget")
        team.budget_used += budget
        d = Delegation(
            delegation_id=str(uuid4()),
            team_id=team.team_id,
            objective=payload["objective"],
            agent_id=aid,
            budget=budget,
            requires_approval=bool(payload.get("requires_approval", False))
        )
        self.delegations[d.delegation_id] = d
        return d
