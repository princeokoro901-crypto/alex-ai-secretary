# Alex Core v2.4 — Agent-to-Agent Handoffs

Adds a structured handoff boundary inside missions.

## Rules
- Both sender and recipient must be assigned to the same mission.
- Sender and recipient must be different agents.
- Recipient must be registered and ready/running.
- Handoffs carry structured findings, evidence, unresolved questions and verification.
- Handoffs do not grant new tools or permissions.

## API
`POST /api/v1/missions/{mission_id}/handoffs`

Payload requires `from_agent`, `to_agent`, and `task_id`; findings/evidence/questions/verification are optional.
