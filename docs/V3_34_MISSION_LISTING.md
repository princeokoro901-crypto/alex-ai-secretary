# v3.34 — Read-only mission listing

## Changes
- Added `MissionService.list()` returning a snapshot of missions currently in memory.
- Added `GET /api/v1/missions`, protected by the existing `missions:read` permission.
- Added a dashboard refresh control, mission cards, and Inspect buttons.
- Successful mission creation refreshes the list.
- Kept mission creation, agent assignment, starting missions, approvals, and external execution separate; the list is read-only.

## Limits
The mission service currently stores missions in process memory. This endpoint lists only missions in the current runtime and does not provide durable storage or pagination. Browser-based visual/interaction testing is not claimed because the browser executable is not available in this environment. No live external integration was tested.
