"""Disposable backup/restore drill for mission SQLite and hash-chained audit JSONL.

All files live in a temporary directory. This is a local development validation,
not a production backup scheduler or disaster-recovery certification.
"""
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audit import AuditLog
from app.missions.models import MissionContract, MissionService, MissionStatus


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(source: Path, destination: Path) -> None:
    """Use SQLite's online backup API rather than copying a potentially live DB file."""
    with sqlite3.connect(str(source), timeout=5) as src, sqlite3.connect(str(destination), timeout=5) as dst:
        src.backup(dst)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="alex-v350-backup-restore-") as tmp:
        root = Path(tmp)
        live_db, live_audit = root / "live.sqlite", root / "live-audit.jsonl"
        service = MissionService(store_path=str(live_db))
        service.create(MissionContract("backup-drill-running", "Verify safe restore of interrupted mission", budget=12.5))
        service.assign_agent("backup-drill-running", "research")
        service.start("backup-drill-running")
        service.close()

        audit = AuditLog(str(live_audit))
        audit.record("backup_drill_fixture", "test-operator", "mission", "backup-drill-running",
                     {"fixture": "disposable", "external_action": False}, event_id="v350-backup-drill-event")
        assert audit.verify() and len(audit.events) == 1
        print("PASS fixture created: running mission in SQLite and valid audit JSONL")

        backup_db, backup_audit = root / "backup.sqlite", root / "backup-audit.jsonl"
        sqlite_backup(live_db, backup_db)
        shutil.copy2(live_audit, backup_audit)
        assert backup_db.stat().st_size > 0 and backup_audit.stat().st_size > 0
        assert sha256(live_audit) == sha256(backup_audit)
        with sqlite3.connect(str(backup_db)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok", integrity
        print("PASS backup: SQLite integrity_check=ok; audit copy SHA-256 matches source")

        # Restore into new paths, never overwrite the fixture/source stores.
        restored_db, restored_audit_path = root / "restored.sqlite", root / "restored-audit.jsonl"
        sqlite_backup(backup_db, restored_db)
        shutil.copy2(backup_audit, restored_audit_path)
        restored_audit = AuditLog(str(restored_audit_path))
        assert restored_audit.verify() and len(restored_audit.events) == 1
        assert restored_audit.events[0].event_id == "v350-backup-drill-event"
        print("PASS restore: audit log reloads with intact hash chain and expected event")

        restored_service = MissionService(store_path=str(restored_db))
        mission = restored_service.get("backup-drill-running")
        assert mission.contract.objective == "Verify safe restore of interrupted mission"
        assert mission.contract.budget == 12.5 and mission.assigned_agents == ["research"]
        assert mission.status == MissionStatus.PAUSED
        assert any(c.get("name") == "recovered_after_restart_manual_review_required" for c in mission.checkpoints)
        assert mission.results == []
        restored_service.close()
        print("PASS restore: mission state recovered, RUNNING converted to PAUSED, no results executed")

        # Re-open after recovery to confirm the conservative state is durable.
        reopened = MissionService(store_path=str(restored_db))
        mission_again = reopened.get("backup-drill-running")
        assert mission_again.status == MissionStatus.PAUSED
        assert len([c for c in mission_again.checkpoints if c.get("name") == "recovered_after_restart_manual_review_required"]) == 1
        reopened.close()
        print("PASS second reopen: paused recovery state persisted without duplicate recovery checkpoint")
        print("LIMIT: disposable local stores only; no production backup target, encryption, scheduler, or external service tested")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL backup/restore drill: {exc}", file=sys.stderr)
        raise SystemExit(1)
