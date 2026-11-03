"""SQLite-backed execution journal. Interrupted running rows become uncertain, never auto-retried."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


def _now():
    return datetime.now(timezone.utc).isoformat()


class ExecutionJournal:
    def __init__(self, path: str):
        if not path:
            raise ValueError("Journal path is required")
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY, permit_id TEXT NOT NULL, mission_id TEXT NOT NULL,
                action_digest TEXT NOT NULL, action_type TEXT NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT, actor_id TEXT, key_id TEXT,
                result_json TEXT, error TEXT)""")
            # A process restart cannot prove whether a handler's external side effect happened.
            db.execute("""CREATE TABLE IF NOT EXISTS execution_reconciliations (
                reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
                actor_id TEXT NOT NULL, key_id TEXT, external_reference TEXT,
                created_at TEXT NOT NULL,
                audit_status TEXT NOT NULL DEFAULT 'pending',
                audit_event_id TEXT,
                FOREIGN KEY(execution_id) REFERENCES executions(execution_id))""")
            # Upgrade journals created by earlier releases without losing history.
            reconciliation_columns = {row[1] for row in db.execute("PRAGMA table_info(execution_reconciliations)").fetchall()}
            if "audit_status" not in reconciliation_columns:
                db.execute("ALTER TABLE execution_reconciliations ADD COLUMN audit_status TEXT NOT NULL DEFAULT 'pending'")
            if "audit_event_id" not in reconciliation_columns:
                db.execute("ALTER TABLE execution_reconciliations ADD COLUMN audit_event_id TEXT")
            db.execute("UPDATE execution_reconciliations SET audit_event_id='execution-reconciliation-' || reconciliation_id WHERE audit_event_id IS NULL")
            # Startup recovery assumes a single active application process per journal path.
            db.execute("UPDATE executions SET status='uncertain', finished_at=?, error='Process restarted while execution was in progress; external outcome requires reconciliation' WHERE status='running'", (_now(),))

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def begin(self, execution_id, permit_id, mission_id, action_digest, action_type, actor):
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO executions (execution_id,permit_id,mission_id,action_digest,action_type,status,started_at,actor_id,key_id) VALUES (?,?,?,?,?,'running',?,?,?)",
                       (execution_id, permit_id, mission_id, action_digest, action_type, _now(), str(actor.get('actor_id','unknown')), actor.get('key_id')))

    def finish(self, execution_id, status, result=None, error=None):
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE executions SET status=?, finished_at=?, result_json=?, error=? WHERE execution_id=? AND status='running'",
                             (status, _now(), json.dumps(result, default=str) if result is not None else None, error, execution_id))
            if cur.rowcount != 1:
                raise RuntimeError("Execution journal state changed unexpectedly")

    def get(self, execution_id):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT execution_id,permit_id,mission_id,action_digest,action_type,status,started_at,finished_at,actor_id,key_id,result_json,error FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
        if not row:
            return None
        keys = ('execution_id','permit_id','mission_id','action_digest','action_type','status','started_at','finished_at','actor_id','key_id','result_json','error')
        item = dict(zip(keys, row))
        if item['result_json']:
            item['result'] = json.loads(item.pop('result_json'))
        else:
            item.pop('result_json')
        return item

    def list(self, limit=100, status=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        allowed_statuses = {"running", "succeeded", "failed", "uncertain"}
        if status is not None and status not in allowed_statuses:
            raise ValueError("status must be running, succeeded, failed, or uncertain")
        with self._lock, self._connect() as db:
            if status is None:
                rows = db.execute("SELECT execution_id FROM executions ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT execution_id FROM executions WHERE status=? ORDER BY started_at DESC, rowid DESC LIMIT ?", (status, limit)).fetchall()
            ids = [r[0] for r in rows]
        return [self.get(i) for i in ids]

    def record_reconciliation(self, execution_id, outcome, reason, actor, external_reference=None):
        allowed = {"confirmed_succeeded", "confirmed_failed", "still_unknown"}
        if outcome not in allowed:
            raise ValueError("Outcome must be confirmed_succeeded, confirmed_failed, or still_unknown")
        if not isinstance(actor, dict) or actor.get("role") != "admin":
            raise PermissionError("Only an administrator may record execution reconciliation")
        actor_id = actor.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("A valid actor_id is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A non-empty reconciliation reason is required")
        with self._lock, self._connect() as db:
            row = db.execute("SELECT status FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
            if row is None:
                raise LookupError("Execution not found")
            if row[0] != "uncertain":
                raise ValueError("Only uncertain executions can be reconciled")
            prior = db.execute("SELECT outcome FROM execution_reconciliations WHERE execution_id=? ORDER BY reconciliation_id DESC LIMIT 1", (execution_id,)).fetchone()
            if prior and prior[0] in {"confirmed_succeeded", "confirmed_failed"}:
                raise ValueError("Execution already has a conclusive reconciliation decision")
            cur = db.execute("""INSERT INTO execution_reconciliations
                (execution_id,outcome,reason,actor_id,key_id,external_reference,created_at,audit_status,audit_event_id)
                VALUES (?,?,?,?,?,?,?,'pending',?)""",
                (execution_id, outcome, reason.strip(), actor_id, actor.get("key_id"),
                 external_reference.strip() if isinstance(external_reference, str) and external_reference.strip() else None,
                 _now(), None))
            rec_id = cur.lastrowid
            event_id = f"execution-reconciliation-{rec_id}"
            db.execute("UPDATE execution_reconciliations SET audit_event_id=? WHERE reconciliation_id=?", (event_id, rec_id))
        return next(item for item in self.reconciliations_for(execution_id) if item["reconciliation_id"] == rec_id)

    def reconciliations_for(self, execution_id):
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT reconciliation_id,execution_id,outcome,reason,actor_id,key_id,
                external_reference,created_at,audit_status,audit_event_id FROM execution_reconciliations
                WHERE execution_id=? ORDER BY reconciliation_id""", (execution_id,)).fetchall()
        keys = ("reconciliation_id", "execution_id", "outcome", "reason", "actor_id", "key_id", "external_reference", "created_at", "audit_status", "audit_event_id")
        return [dict(zip(keys, row)) for row in rows]

    def pending_reconciliation_audits(self, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT reconciliation_id,execution_id,outcome,reason,actor_id,key_id,
                external_reference,created_at,audit_status,audit_event_id FROM execution_reconciliations
                WHERE audit_status='pending' ORDER BY reconciliation_id LIMIT ?""", (limit,)).fetchall()
        keys = ("reconciliation_id", "execution_id", "outcome", "reason", "actor_id", "key_id", "external_reference", "created_at", "audit_status", "audit_event_id")
        return [dict(zip(keys, row)) for row in rows]

    def mark_reconciliation_audit_status(self, reconciliation_id, status):
        if status not in {"pending", "recorded"}:
            raise ValueError("Audit status must be pending or recorded")
        with self._lock, self._connect() as db:
            cur = db.execute("UPDATE execution_reconciliations SET audit_status=? WHERE reconciliation_id=?", (status, reconciliation_id))
            if cur.rowcount != 1:
                raise LookupError("Reconciliation record not found")
        return next((item for item in self.reconciliations_for_execution_reconciliation_id(reconciliation_id)), None)

    def reconciliations_for_execution_reconciliation_id(self, reconciliation_id):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT execution_id FROM execution_reconciliations WHERE reconciliation_id=?", (reconciliation_id,)).fetchone()
        if row is None:
            return []
        return [item for item in self.reconciliations_for(row[0]) if item["reconciliation_id"] == reconciliation_id]

    def uncertain(self):
        return self.list(limit=500, status="uncertain")
