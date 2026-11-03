# v2.1 AI Workforce

Alex now manages specialized AI workers through bounded teams.

Core objects:
- Agent: role, capabilities, tools, permissions, memory scope, trust level.
- Team: objective, owner, members, budget and status.
- Delegation: objective, agent, budget, deadline and approval requirement.
- Handoff: structured findings, evidence, unresolved questions and verification.
- PerformanceRecord: completion, failure, confidence, spend and verification.

Guardrails:
1. Capability does not equal authority.
2. Agents receive only scoped context and tools.
3. Team budgets are checked before delegation.
4. Agents must belong to the assigned team.
5. Sensitive actions remain behind Alex's policy/risk/approval boundary.
6. Agent handoffs are structured.

Next: v2.2 Autonomous Missions — mission contracts, checkpoints, deadlines,
forbidden actions, verification, stop conditions and controlled autonomy.
