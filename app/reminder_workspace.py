"""Local workspace task: reminders.

Same classification as draft_report (app/workspace.py): local-only, no external calls, no
third party contacted, nothing sent anywhere. A reminder is persisted (unlike a one-shot
report draft) so it can be listed later, but creating one carries the same risk profile as
drafting a report, so it follows the same no-approval, audited-on-write pattern rather than
going through ApprovalService. If a future workflow lets a reminder *trigger* an external
action (a notification, a connector call), that trigger should go through the approval and
execution-permit path like any other consequential action; this module only ever creates and
reads local rows.

When the free-text "when" matches a recognised English phrase, a concrete UTC due time is
computed by this module's own deterministic parser -- never by the model. Anything it can't
parse still creates the reminder with its original text; it simply has no computed due time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
import re
import sqlite3
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WorkspaceInputError(ValueError):
    """Raised when a local workspace task has invalid or oversized input. Mirrors
    app.workspace.WorkspaceInputError so main.py can catch one exception type for every
    workspace task; kept as a separate class (not imported) so this module has no dependency
    on app.workspace beyond the shared contract."""


MAX_TITLE_CHARS = 160
MAX_WHEN_CHARS = 160

# ---------------------------------------------------------------------------
# Deterministic when-text parser (trusted code; the model only supplies the phrase)
# ---------------------------------------------------------------------------
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAYPART_DEFAULT = {"morning": (9, 0), "afternoon": (15, 0), "evening": (19, 0), "night": (21, 0)}
_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_WEEKDAY_RE = re.compile(r"\b(?:next\s+)?(" + "|".join(_WEEKDAYS) + r")\b", re.I)


def server_timezone():
    """ALEX_TIMEZONE if it names a real IANA zone, else UTC. Never raises."""
    name = os.getenv("ALEX_TIMEZONE", "UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _time_of_day(text: str):
    match = _TIME_RE.search(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = (match.group(3) or "").lower()
    if not 0 <= minute <= 59:
        return None
    if suffix == "pm" and hour != 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23:
        return None
    return hour, minute


def parse_when(text, now: datetime | None = None, tz=None) -> datetime | None:
    """Recognised phrases: today, tonight, tomorrow (+ optional morning/afternoon/evening),
    a weekday name (bare or "next <day>" -- both mean the nearest such day strictly after
    today, not necessarily next calendar week), and an "at H(:MM)(am/pm)" clause, alone or
    combined with any of the above. Returns a UTC datetime, or None if nothing matched.
    ``now``/``tz`` are injectable for deterministic tests; callers otherwise get the real
    clock and ALEX_TIMEZONE."""
    if not isinstance(text, str) or not text.strip():
        return None
    original = text.strip().lower()
    tz = tz or server_timezone()
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now.date()
    explicit_time = _time_of_day(original)

    due_date: date | None = None
    default_time = None
    date_was_explicit = True
    if "today" in original:
        due_date = today
    elif "tonight" in original:
        due_date, default_time = today, _DAYPART_DEFAULT["night"]
    elif "tomorrow" in original:
        due_date = today + timedelta(days=1)
        for part in ("morning", "afternoon", "evening", "night"):
            if part in original:
                default_time = _DAYPART_DEFAULT[part]
                break
    else:
        weekday = _WEEKDAY_RE.search(original)
        if weekday:
            target = _WEEKDAYS.index(weekday.group(1).lower())
            delta = (target - today.weekday()) % 7
            due_date = today + timedelta(days=delta or 7)  # always a future day, never today
        elif explicit_time:
            due_date, date_was_explicit = today, False

    if due_date is None:
        return None
    hour, minute = explicit_time or default_time or (9, 0)
    candidate = datetime(due_date.year, due_date.month, due_date.day, hour, minute, tzinfo=tz)
    if not date_was_explicit and candidate <= now:
        candidate += timedelta(days=1)  # a bare time that has already passed today means tomorrow
    return candidate.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
@dataclass
class Reminder:
    reminder_id: str
    title: str
    when_text: str | None
    due_at: str | None
    created_at: str


class ReminderStore:
    """SQLite-backed; ALEX_REMINDER_DB_PATH makes it durable, in-memory otherwise."""

    def __init__(self, path: str | None = None, clock=None):
        self.path = path or ":memory:"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("""CREATE TABLE IF NOT EXISTS reminders (
            reminder_id TEXT PRIMARY KEY, title TEXT NOT NULL, when_text TEXT,
            due_at TEXT, created_at TEXT NOT NULL)""")
        if self.path != ":memory:" and os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @property
    def persistent(self) -> bool:
        return self.path != ":memory:"

    @staticmethod
    def _row(row) -> Reminder:
        return Reminder(reminder_id=row[0], title=row[1], when_text=row[2], due_at=row[3], created_at=row[4])

    def create(self, title: str, when_text: str | None) -> Reminder:
        now = self._clock()
        due = parse_when(when_text, now=now)
        rid = str(uuid4())
        created = now.isoformat(timespec="seconds")
        with self._lock:
            self._db.execute("INSERT INTO reminders VALUES (?,?,?,?,?)",
                             (rid, title, when_text, due.isoformat() if due else None, created))
        return self._row((rid, title, when_text, due.isoformat() if due else None, created))

    def get(self, reminder_id: str) -> Reminder | None:
        if not isinstance(reminder_id, str):
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT reminder_id, title, when_text, due_at, created_at FROM reminders WHERE reminder_id=?",
                (reminder_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 100) -> list[Reminder]:
        with self._lock:
            rows = self._db.execute(
                "SELECT reminder_id, title, when_text, due_at, created_at FROM reminders "
                "ORDER BY (due_at IS NULL), due_at ASC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ---------------------------------------------------------------------------
# Workspace task entry point (matches app.workspace.draft_report's shape/contract)
# ---------------------------------------------------------------------------
def create_reminder(payload: dict, store: ReminderStore) -> dict:
    if not isinstance(payload, dict):
        raise WorkspaceInputError("Request body must be a JSON object")
    title = payload.get("title")
    when_text = payload.get("when")
    if not isinstance(title, str) or not title.strip():
        raise WorkspaceInputError("A reminder title is required")
    clean_title = re.sub(r"\s+", " ", title.strip())
    if len(clean_title) > MAX_TITLE_CHARS:
        raise WorkspaceInputError(f"Reminder title must be {MAX_TITLE_CHARS} characters or fewer")
    if when_text is not None:
        if not isinstance(when_text, str):
            raise WorkspaceInputError("'when' must be text if provided")
        when_text = re.sub(r"\s+", " ", when_text.strip()) or None
        if when_text and len(when_text) > MAX_WHEN_CHARS:
            raise WorkspaceInputError(f"'when' must be {MAX_WHEN_CHARS} characters or fewer")

    reminder = store.create(clean_title, when_text)
    checks = {
        "title_present": bool(reminder.title),
        "stored_and_readable": store.get(reminder.reminder_id) is not None,
        "due_at_is_utc_or_absent": reminder.due_at is None or reminder.due_at.endswith("+00:00"),
    }
    verified = all(checks.values())
    if not verified:
        raise RuntimeError("Reminder creation failed its integrity checks")

    return {
        "task_id": reminder.reminder_id,
        "task_type": "create_reminder",
        "status": "completed",
        "execution_mode": "local_store",
        "created_at": reminder.created_at,
        "title": reminder.title,
        "when": reminder.when_text,
        "due_at": reminder.due_at,
        "verification": {"passed": verified, "checks": checks},
        "external_actions_performed": False,
        "ai_model_used": False,
        "notice": ("Saved to the reminder database." if store.persistent else
                   "Saved in memory only; it will not survive a restart.") + (
                   "" if reminder.due_at else
                   " I couldn't work out a specific due time from that; the reminder is saved with its original text."),
    }
