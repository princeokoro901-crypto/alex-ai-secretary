# Alex v2.3 — Agent Execution Runtime

v2.3 connects autonomous missions to the AI Workforce through a scoped execution runtime.

## Boundary checks
- Mission must be RUNNING.
- Agent must be assigned to the mission.
- Agent must be registered and available.
- Tool must be in the agent's tool allowlist.
- Tool must be allowed by the mission contract.
- Task budget cannot exceed the mission budget.
- Execution returns a structured result with verification metadata.

## API
`POST /api/v1/missions/{mission_id}/agents/{agent_id}/execute`

Example body:
```json
{"objective":"Find launch-system sources","tool":"search_web","input":{"query":"launch systems"},"budget":2}
```

This release uses a deterministic execution stub. Real browser/search/tool providers are intentionally deferred until the permission, verification, and audit boundaries are hardened.
