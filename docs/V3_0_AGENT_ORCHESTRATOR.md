# Alex v3.0 — Agent Orchestrator

## Purpose
Turn the workforce, missions, routing, runtime, verification, and recovery layers into one controlled end-to-end orchestration path.

## Flow
User request -> task requirements -> authorized routing -> mission -> runtime -> verification/recovery -> result.

## Authority rule
Routing is advisory and capability constrained. The runtime re-checks workforce registration, mission assignment, tool authorization, mission tool policy, budget, and execution state before work occurs. Performance never grants authority.

## API
`POST /api/v1/orchestrate`

Example payload:
```json
{
  "objective": "Research aerospace scholarships",
  "task_type": "research",
  "required_tools": ["search_web"],
  "tool": "search_web",
  "budget": 2,
  "input": {}
}
```

## Failure behavior
Failed/timeout/uncertain agent execution is delegated to the existing recovery service. The orchestrator pauses a still-running mission after a non-successful result so recovery or human intervention can proceed without silently treating failure as success.
