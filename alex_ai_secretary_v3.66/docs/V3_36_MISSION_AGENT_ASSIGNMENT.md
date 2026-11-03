# v3.36 — Mission Agent Assignment Workspace

## Change
The same-origin dashboard now loads registered agent inventory and offers an assignment control for the currently inspected mission. Assignment calls the existing `POST /api/v1/missions/{mission_id}/agents` endpoint. It does not start the mission, execute agent tools, approve actions, or grant additional permissions.

## Safety
- The browser uses the existing API key authorization path.
- The backend remains responsible for role checks and rejecting unknown agent IDs.
- Mission results continue to be rendered using `textContent`, not HTML injection.
- Assignment and mission execution remain separate operations.

## Validation limits
Automated tests, syntax checks, and archive checks are recorded in `TEST_RESULTS.txt`. No full browser automation, visual inspection, or live external integration was performed for this milestone.
