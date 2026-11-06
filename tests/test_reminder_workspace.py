"""v3.67: the create_reminder local workspace task and its when-text parser."""
from datetime import datetime, timezone

from app.reminder_workspace import (
    ReminderStore, WorkspaceInputError, create_reminder, parse_when, server_timezone,
)

MON_10AM = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_when
# ---------------------------------------------------------------------------
def test_unrecognised_or_empty_text_returns_none():
    for text in (None, "", "   ", 5, [], "whenever is fine", "soonish", "ASAP"):
        assert parse_when(text, now=MON_10AM) is None


def test_today_keeps_the_date_even_if_the_time_already_passed():
    assert parse_when("today at 9am", now=MON_10AM) == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def test_today_without_a_time_defaults_to_9am():
    assert parse_when("today", now=MON_10AM) == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def test_tonight_defaults_to_9pm():
    assert parse_when("tonight", now=MON_10AM) == datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)


def test_tomorrow_default_and_dayparts():
    assert parse_when("tomorrow", now=MON_10AM) == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    assert parse_when("tomorrow morning", now=MON_10AM) == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    assert parse_when("tomorrow afternoon", now=MON_10AM) == datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    assert parse_when("tomorrow evening", now=MON_10AM) == datetime(2026, 10, 27, 19, 0, tzinfo=timezone.utc)


def test_tomorrow_with_explicit_time_overrides_daypart():
    assert parse_when("tomorrow evening at 6:30am", now=MON_10AM) == datetime(2026, 10, 27, 6, 30, tzinfo=timezone.utc)


def test_bare_time_rolls_to_tomorrow_only_if_already_passed():
    assert parse_when("at 5pm", now=MON_10AM) == datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc)
    assert parse_when("at 9am", now=MON_10AM) == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def test_weekday_is_always_a_future_day_never_today():
    assert parse_when("monday", now=MON_10AM) == datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    assert parse_when("friday", now=MON_10AM) == datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
    assert parse_when("on friday at 3pm", now=MON_10AM) == datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
    assert parse_when("next friday", now=MON_10AM) == parse_when("friday", now=MON_10AM)  # documented simplification


def test_invalid_time_values_are_rejected():
    for text in ("at 13pm", "at 25:00", "at 5:99am", "at 99"):
        assert parse_when(text, now=MON_10AM) is None


def test_timezone_changes_the_computed_utc_time():
    from zoneinfo import ZoneInfo
    lagos = ZoneInfo("Africa/Lagos")  # UTC+1, no DST: stable for a deterministic test
    assert parse_when("today at 9am", now=MON_10AM, tz=lagos) == datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


def test_server_timezone_falls_back_to_utc_for_an_unknown_name(monkeypatch):
    monkeypatch.setenv("ALEX_TIMEZONE", "Not/AZone")
    assert server_timezone() == timezone.utc
    monkeypatch.setenv("ALEX_TIMEZONE", "Africa/Lagos")
    assert str(server_timezone()) == "Africa/Lagos"


def test_hostile_or_long_input_is_handled_quickly_and_safely():
    import time
    start = time.perf_counter()
    for t in ["remind " * 3000, "at " + "9" * 3000, "tomorrow " * 3000, "a" * 5000]:
        parse_when(t, now=MON_10AM)
    assert time.perf_counter() - start < 1.0


# ---------------------------------------------------------------------------
# ReminderStore
# ---------------------------------------------------------------------------
def test_store_is_in_memory_by_default():
    store = ReminderStore()
    assert store.persistent is False
    r = store.create("Pay rent", "friday")
    assert store.get(r.reminder_id).title == "Pay rent"


def test_store_survives_a_restart(tmp_path):
    path = str(tmp_path / "reminders.sqlite3")
    a = ReminderStore(path)
    r = a.create("Call mum", "tomorrow")
    a.close()
    b = ReminderStore(path)
    assert b.persistent is True
    assert b.get(r.reminder_id).title == "Call mum"
    if hasattr(__import__("os"), "name"):
        import os
        if os.name == "posix":
            assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_list_orders_soonest_due_first_then_undated_last():
    fixed = MON_10AM
    store = ReminderStore(clock=lambda: fixed)
    store.create("No time", "soonish")
    store.create("Later", "friday")
    store.create("Sooner", "tomorrow")
    titles = [r.title for r in store.list()]
    assert titles == ["Sooner", "Later", "No time"]


# ---------------------------------------------------------------------------
# create_reminder (the workspace task entry point)
# ---------------------------------------------------------------------------
def test_create_reminder_computes_due_at_and_matches_workspace_contract():
    store = ReminderStore(clock=lambda: MON_10AM)
    result = create_reminder({"title": "Study physics", "when": "tomorrow morning"}, store)
    assert result["status"] == "completed"
    assert result["task_type"] == "create_reminder"
    assert result["due_at"] == "2026-10-27T09:00:00+00:00"
    assert result["external_actions_performed"] is False
    assert result["ai_model_used"] is False
    assert result["verification"]["passed"] is True


def test_create_reminder_without_a_recognised_when_still_creates_it():
    store = ReminderStore(clock=lambda: MON_10AM)
    result = create_reminder({"title": "Something", "when": "soonish"}, store)
    assert result["status"] == "completed"
    assert result["due_at"] is None
    assert "couldn't work out" in result["notice"]


def test_create_reminder_without_when_at_all():
    store = ReminderStore(clock=lambda: MON_10AM)
    result = create_reminder({"title": "No time given"}, store)
    assert result["due_at"] is None


def test_missing_or_blank_title_is_rejected():
    store = ReminderStore()
    for payload in ({}, {"title": ""}, {"title": "   "}, {"title": 5}, "not-a-dict"):
        try:
            create_reminder(payload, store)
            assert False, f"expected WorkspaceInputError for {payload!r}"
        except WorkspaceInputError:
            pass


def test_oversized_title_or_when_is_rejected():
    store = ReminderStore()
    for payload in ({"title": "x" * 161}, {"title": "ok", "when": "x" * 161}):
        try:
            create_reminder(payload, store)
            assert False, f"expected WorkspaceInputError for {payload!r}"
        except WorkspaceInputError:
            pass


def test_whitespace_is_normalised():
    store = ReminderStore(clock=lambda: MON_10AM)
    result = create_reminder({"title": "  Study   physics  ", "when": "  tomorrow  "}, store)
    assert result["title"] == "Study physics"


def test_persistent_notice_differs_from_in_memory(tmp_path):
    mem_store = ReminderStore(clock=lambda: MON_10AM)
    mem_result = create_reminder({"title": "A"}, mem_store)
    assert "in memory only" in mem_result["notice"]

    disk_store = ReminderStore(str(tmp_path / "r.sqlite3"), clock=lambda: MON_10AM)
    disk_result = create_reminder({"title": "B"}, disk_store)
    assert "reminder database" in disk_result["notice"]
