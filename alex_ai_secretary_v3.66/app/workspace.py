"""Small, local-only task workflows that create inspectable artifacts.

This first workflow is deliberately deterministic: it formats user-supplied notes
into a reviewable Markdown report. It does not call an LLM or external service.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from uuid import uuid4


class WorkspaceInputError(ValueError):
    """Raised when a local workspace task has invalid or oversized input."""


def draft_report(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise WorkspaceInputError("Request body must be a JSON object")

    title = payload.get("title")
    notes = payload.get("notes")
    audience = payload.get("audience", "General audience")
    if not isinstance(title, str) or not title.strip():
        raise WorkspaceInputError("A report title is required")
    if len(title.strip()) > 160:
        raise WorkspaceInputError("Report title must be 160 characters or fewer")
    if not isinstance(notes, str) or not notes.strip():
        raise WorkspaceInputError("Source notes are required")
    if len(notes) > 12000:
        raise WorkspaceInputError("Source notes must be 12,000 characters or fewer")
    if not isinstance(audience, str) or len(audience.strip()) > 120:
        raise WorkspaceInputError("Audience must be 120 characters or fewer")

    clean_title = re.sub(r"\s+", " ", title.strip())
    clean_audience = re.sub(r"\s+", " ", audience.strip()) or "General audience"
    source_lines = [line.strip() for line in notes.splitlines() if line.strip()]
    if not source_lines:
        raise WorkspaceInputError("Source notes must contain at least one non-empty line")

    # This workflow makes no semantic claims: every key point is copied from a
    # non-empty source line so the user can trace the draft back to the input.
    bullets = "\n".join(f"- {line}" for line in source_lines)
    source_block = "\n".join(f"> {line}" for line in notes.strip().splitlines())
    task_id = f"draft-{uuid4()}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = (
        f"# {clean_title}\n\n"
        f"**Audience:** {clean_audience}  \n"
        f"**Prepared (UTC):** {created_at}  \n"
        f"**Draft status:** Requires human review\n\n"
        "## Overview\n\n"
        "This report draft was assembled locally from the supplied notes. "
        "It has not been independently fact-checked, and no external research was performed.\n\n"
        "## Key points from the notes\n\n"
        f"{bullets}\n\n"
        "## Review checklist\n\n"
        "- Verify names, dates, figures, and factual claims.\n"
        "- Add evidence or references where needed.\n"
        "- Confirm the conclusions and next steps before sharing.\n\n"
        "## Source notes (preserved)\n\n"
        f"{source_block}\n"
    )
    digest = sha256(report.encode("utf-8")).hexdigest()
    checks = {
        "title_present": clean_title in report,
        "source_lines_preserved": all(line in report for line in source_lines),
        "review_warning_present": "has not been independently fact-checked" in report,
        "output_nonempty": bool(report.strip()),
    }
    verified = all(checks.values())
    if not verified:
        raise RuntimeError("Generated report failed its integrity checks")

    return {
        "task_id": task_id,
        "task_type": "draft_report_from_notes",
        "status": "completed",
        "execution_mode": "local_template",
        "created_at": created_at,
        "title": clean_title,
        "audience": clean_audience,
        "source_line_count": len(source_lines),
        "output_markdown": report,
        "output_sha256": digest,
        "verification": {"passed": verified, "checks": checks},
        "external_actions_performed": False,
        "ai_model_used": False,
        "notice": "This is a deterministic local draft, not an AI-generated or fact-checked report. Review before use.",
    }
