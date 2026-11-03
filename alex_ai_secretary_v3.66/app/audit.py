import hashlib, json, os, shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

@dataclass
class AuditEvent:
    event_id: str
    event_type: str
    actor: str
    resource_type: str
    resource_id: str
    timestamp: str
    details: dict
    prev_hash: str = ""
    event_hash: str = ""

class AuditIntegrityError(ValueError):
    pass

class AuditLog:
    # Audit records must never become a secondary secret store.
    _SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "authorization", "credential", "private_key")

    @classmethod
    def _safe_details(cls, value):
        if isinstance(value, dict):
            safe = {}
            for key, item in value.items():
                name = str(key).lower().replace("-", "_")
                if any(part in name for part in cls._SENSITIVE_KEY_PARTS):
                    safe[str(key)] = "[REDACTED]"
                else:
                    safe[str(key)] = cls._safe_details(item)
            return safe
        if isinstance(value, (list, tuple)):
            return [cls._safe_details(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def __init__(self, path: str|None=None):
        self.path = path
        self._lock = RLock()
        self.events: list[AuditEvent] = []
        self.recovery_path = None
        self._load()

    @staticmethod
    def _hash_payload(event: AuditEvent) -> str:
        data = asdict(event).copy(); data.pop("event_hash", None)
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def _load(self):
        if not self.path or not os.path.exists(self.path): return
        with self._lock:
            try:
                loaded=[]
                # A crash may leave only the final JSONL record partially written.
                # Repair only an unterminated, malformed final line; malformed
                # newline-terminated records and hash-chain failures remain fatal.
                with open(self.path, 'rb') as f:
                    raw_bytes = f.read()
                lines = raw_bytes.splitlines(keepends=True)
                parsed_lines = []
                offset = 0
                for index, line in enumerate(lines):
                    is_final = index == len(lines) - 1
                    try:
                        decoded = line.decode('utf-8')
                        if decoded.strip():
                            raw = json.loads(decoded)
                            raw.setdefault('prev_hash', '')
                            raw.setdefault('event_hash', '')
                            parsed_lines.append(AuditEvent(**raw))
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
                        if is_final and not line.endswith((b'\n', b'\r')):
                            tail_path = f"{self.path}.partial"
                            with open(tail_path, 'wb') as tail:
                                tail.write(line)
                                tail.flush(); os.fsync(tail.fileno())
                            with open(self.path, 'wb') as repaired:
                                repaired.write(raw_bytes[:offset])
                                repaired.flush(); os.fsync(repaired.fileno())
                            break
                        raise
                    offset += len(line)
                loaded = parsed_lines
                prev=''
                for event in loaded:
                    if event.prev_hash != prev or (event.event_hash and event.event_hash != self._hash_payload(event)):
                        raise AuditIntegrityError('Audit hash-chain verification failed')
                    if not event.event_hash:
                        event.event_hash=self._hash_payload(event)
                    prev=event.event_hash
                self.events=loaded
            except (OSError, json.JSONDecodeError, TypeError, KeyError, AuditIntegrityError):
                self.events=[]
                self.recovery_path=f"{self.path}.corrupt"
                try:
                    shutil.move(self.path, self.recovery_path)
                except OSError:
                    self.recovery_path=None

    def record(self, event_type: str, actor: str, resource_type: str, resource_id: str, details: dict|None=None, event_id: str|None=None):
        with self._lock:
            if event_id is not None:
                existing = next((item for item in self.events if item.event_id == event_id), None)
                if existing is not None:
                    expected_details = self._safe_details(details or {})
                    if (existing.event_type, existing.actor, existing.resource_type, existing.resource_id, existing.details) != (event_type, actor, resource_type, resource_id, expected_details):
                        raise AuditIntegrityError("Idempotency event ID conflicts with an existing audit event")
                    return existing
            prev=self.events[-1].event_hash if self.events else ''
            event = AuditEvent(event_id or str(uuid4()), event_type, actor, resource_type, resource_id, datetime.now(timezone.utc).isoformat(), details or {}, prev, '')
            event.event_hash=self._hash_payload(event)
            event.details = self._safe_details(details or {})
            event.event_hash = self._hash_payload(event)
            if self.path:
                # Persist before exposing the event in memory. A failed write must
                # not advance the in-memory chain beyond the durable log.
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + '\n')
                    f.flush(); os.fsync(f.fileno())
            self.events.append(event)
        return event

    def verify(self):
        with self._lock:
            prev=''
            for event in self.events:
                if event.prev_hash != prev or event.event_hash != self._hash_payload(event):
                    return False
                prev=event.event_hash
            return True

    def list(self, resource_type=None, resource_id=None):
        with self._lock:
            return [e for e in self.events if (resource_type is None or e.resource_type == resource_type) and (resource_id is None or e.resource_id == resource_id)]
