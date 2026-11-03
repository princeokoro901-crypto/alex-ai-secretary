from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now():
    return datetime.now(timezone.utc)


@dataclass
class Evidence:
    evidence_id: str
    source: str
    claim: str
    verified: bool = False
    method: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationRecord:
    verification_id: str
    task_id: str
    verified: bool
    status: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    verified_at: datetime = field(default_factory=now)


class VerificationService:
    """Records evidence and verification without granting new authority."""

    def __init__(self):
        self.evidence: dict[str, Evidence] = {}
        self.records: dict[str, VerificationRecord] = {}

    def add_evidence(self, source: str, claim: str, verified: bool = False, method: str | None = None, metadata=None):
        if not source or not claim:
            raise ValueError("Evidence source and claim are required")
        item = Evidence(str(uuid4()), source, claim, verified, method, metadata or {})
        self.evidence[item.evidence_id] = item
        return item

    def verify(self, task_id: str, checks: list[dict[str, Any]], evidence_ids=None, unresolved_questions=None):
        evidence_ids = evidence_ids or []
        for eid in evidence_ids:
            if eid not in self.evidence:
                raise ValueError("Unknown evidence reference")
        verified = bool(checks) and all(bool(c.get("passed")) for c in checks)
        unresolved = unresolved_questions or []
        if unresolved:
            verified = False
        record = VerificationRecord(
            verification_id=str(uuid4()), task_id=task_id,
            verified=verified, status="verified" if verified else "uncertain",
            checks=checks, evidence_ids=evidence_ids,
            unresolved_questions=unresolved,
        )
        self.records[task_id] = record
        return record
