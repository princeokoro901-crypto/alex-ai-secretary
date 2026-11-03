# v3.63 — First practical local workspace workflow

## What changed

- Added `POST /api/v1/workspace/draft-report`, restricted to the existing `missions:write` permission (operator/admin by default; readers are denied).
- The workflow turns a title, optional audience, and source notes into a reviewable Markdown report using a deterministic local template.
- The output preserves each non-empty source line as a key point and includes the original notes, a review checklist, a SHA-256 digest, and explicit verification checks.
- Added a dashboard form, preview, and Markdown download action.
- The workflow makes no LLM/provider calls and performs no external actions. It does not claim to fact-check or semantically summarize the source notes.
- Input is bounded: title 160 characters, audience 120 characters, notes 12,000 characters.
- Successful task creation emits an audit event with metadata only; note contents are not copied into the audit payload.

## Endpoint

`POST /api/v1/workspace/draft-report`

Example body:

```json
{
  "title": "Prototype Progress",
  "audience": "Project team",
  "notes": "Dashboard is running locally.\nRole tests pass.\nNext: verify direct browser networking."
}
```

The response includes `status`, `task_id`, `output_markdown`, `output_sha256`, `verification`, `ai_model_used: false`, and `external_actions_performed: false`.

## Validation

- `tests/test_v363_practical_report_workflow.py` covers the successful operator path, reader denial, bounded-input validation, output verification, dashboard controls, and health version.
- Existing permission inventory tests ensure the new API route has an explicit permission rule.
- This is a deterministic local task workflow, not an integrated LLM workflow. A true natural-language AI workflow still requires an explicitly configured model provider, error handling, and separate validation.
