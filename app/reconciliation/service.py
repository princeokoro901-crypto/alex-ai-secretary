from dataclasses import dataclass


@dataclass(frozen=True)
class ReconciliationIssue:
    issue_type: str
    orchestration_id: str
    classification: str
    detail: str


class LifecycleReconciliationService:
    """Read-only cross-store lifecycle reconciliation.

    It compares durable orchestration state with the audit lifecycle. It never
    repairs, deletes, replays, or mutates either store.
    """

    COMPLETION_EVENTS = {"orchestration_completed"}
    INTERRUPTED_EVENTS = {"orchestration_interrupted"}
    TERMINAL_EVENTS = COMPLETION_EVENTS | INTERRUPTED_EVENTS
    REPLAY_EVENTS = {"orchestration_replayed"}
    START_EVENTS = {"orchestration_started"}

    def reconcile(self, runs: dict, audit_events: list) -> dict:
        by_id: dict[str, list] = {}
        for event in audit_events:
            if event.resource_type == "orchestration":
                by_id.setdefault(event.resource_id, []).append(event)

        issues: list[ReconciliationIssue] = []
        for oid, events in by_id.items():
            starts = [e for e in events if e.event_type in self.START_EVENTS]
            completions = [e for e in events if e.event_type in self.COMPLETION_EVENTS]
            terminal = [e for e in events if e.event_type in self.TERMINAL_EVENTS]
            replays = [e for e in events if e.event_type in self.REPLAY_EVENTS]

            if len(completions) > 1:
                issues.append(ReconciliationIssue(
                    "duplicate_completion", oid, "inconsistent",
                    "More than one completion event exists for the orchestration."
                ))
            if starts and not terminal and oid not in runs:
                issues.append(ReconciliationIssue(
                    "incomplete_lifecycle", oid, "recoverable",
                    "A start event exists without a persisted orchestration result or interruption record."
                ))
            if starts and terminal and oid not in runs:
                issues.append(ReconciliationIssue(
                    "completed_without_state", oid, "inconsistent",
                    "A terminal orchestration audit event exists but durable orchestration state is missing."
                ))
            if replays and oid not in runs:
                issues.append(ReconciliationIssue(
                    "replay_without_state", oid, "inconsistent",
                    "A replay event references an orchestration absent from durable state."
                ))

        for oid in runs:
            events = by_id.get(oid, [])
            terminal = [e for e in events if e.event_type in self.TERMINAL_EVENTS]
            if not terminal:
                issues.append(ReconciliationIssue(
                    "state_without_completion", oid, "recoverable",
                    "Durable orchestration state exists without a terminal lifecycle audit event."
                ))

        recoverable = sum(i.classification == "recoverable" for i in issues)
        inconsistent = sum(i.classification == "inconsistent" for i in issues)
        return {
            "consistent": not issues,
            "orchestration_count": len(runs),
            "audit_orchestration_count": len(by_id),
            "recoverable_count": recoverable,
            "inconsistent_count": inconsistent,
            "issues": [{"type": i.issue_type, "orchestration_id": i.orchestration_id, "classification": i.classification, "detail": i.detail} for i in issues],
        }
