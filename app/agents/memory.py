from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now():
    return datetime.now(timezone.utc)


@dataclass
class MemoryItem:
    memory_id: str
    key: str
    value: Any
    scope: str
    owner_id: str
    sensitivity: str = "normal"
    importance: float = 0.5
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


class ScopedMemoryService:
    """Small development memory store with explicit scope filtering."""
    def __init__(self):
        self.items: dict[str, MemoryItem] = {}

    def remember(self, owner_id: str, key: str, value: Any, scope: str, sensitivity: str = "normal", importance: float = 0.5):
        if not owner_id or not key or not scope:
            raise ValueError("Memory owner, key, and scope are required")
        if sensitivity not in {"normal", "sensitive", "restricted"}:
            raise ValueError("Unsupported memory sensitivity")
        item = MemoryItem(str(uuid4()), key, value, scope, owner_id, sensitivity, importance)
        self.items[item.memory_id] = item
        return item

    def build_context(self, owner_id: str, allowed_scopes: list[str], max_sensitivity: str = "normal", limit: int = 20):
        rank = {"normal": 0, "sensitive": 1, "restricted": 2}
        if max_sensitivity not in rank:
            raise ValueError("Unsupported maximum sensitivity")
        items = [
            x for x in self.items.values()
            if x.owner_id == owner_id and x.scope in allowed_scopes and rank[x.sensitivity] <= rank[max_sensitivity]
        ]
        items.sort(key=lambda x: (x.importance, x.updated_at), reverse=True)
        return items[:limit]

    def context_dicts(self, owner_id: str, allowed_scopes: list[str], max_sensitivity: str = "normal", limit: int = 20):
        return [
            {"memory_id": x.memory_id, "key": x.key, "value": x.value, "scope": x.scope, "sensitivity": x.sensitivity}
            for x in self.build_context(owner_id, allowed_scopes, max_sensitivity, limit)
        ]
