from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

class RecoveryAction(str, Enum):
    RETRY = "retry"
    ESCALATE = "escalate"
    STOP = "stop"

@dataclass
class RecoveryPolicy:
    max_retries: int = 2
    retryable_statuses: set[str] = field(default_factory=lambda: {"failed", "timeout", "uncertain"})

@dataclass
class RecoveryRecord:
    recovery_id: str
    task_id: str
    action: RecoveryAction
    attempt: int
    reason: str
    original_task_id: str

class RecoveryService:
    def __init__(self, policy: RecoveryPolicy | None = None):
        self.policy = policy or RecoveryPolicy()
        self.records: list[RecoveryRecord] = []
        self.attempts: dict[str, int] = {}

    def decide(self, task_id: str, status: str, reason: str = "", recovery_key: str | None = None) -> RecoveryRecord:
        key = recovery_key or task_id
        attempt = self.attempts.get(key, 0)
        if status not in self.policy.retryable_statuses:
            action = RecoveryAction.STOP
        elif attempt < self.policy.max_retries:
            action = RecoveryAction.RETRY
            attempt += 1
            self.attempts[key] = attempt
        else:
            action = RecoveryAction.ESCALATE
        record = RecoveryRecord(str(uuid4()), task_id, action, attempt, reason, task_id)
        self.records.append(record)
        return record
