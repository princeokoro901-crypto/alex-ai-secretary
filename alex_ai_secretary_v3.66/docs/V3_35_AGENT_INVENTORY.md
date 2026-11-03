# v3.35 — Read-only registered-agent inventory

## Change
- Added `GET /api/v1/workforce/agents`, returning the currently registered agents and their declared capabilities.
- Endpoint requires `workforce:read`; reader roles may inspect the inventory but cannot assign agents because assignment remains protected by `missions:write`.
- This is groundwork for the mission assignment UI, not the UI itself.

## Validation
- Automated suite: see `TEST_RESULTS.txt`.
- Tests check registered-agent presence, inventory read-only behavior, and reader-role denial of assignment.
- No browser automation or external integration was performed for this milestone.

## Limitations
- Agent inventory reflects agents registered in the current application process.
- The endpoint does not assign, start, delegate, or execute tasks.
