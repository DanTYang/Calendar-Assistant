"""Part 3 — answering questions with plain Python."""

from datetime import datetime, timedelta

import pytest

from conftest import NOW


def days(text):
    from assistant.queries import parse_when

    start, end = parse_when(text, NOW)
    return start.date().isoformat(), end.date().isoformat()


def test_parse_when_single_days():
    assert days("today") == ("2026-08-03", "2026-08-04")
    assert days("tomorrow") == ("2026-08-04", "2026-08-05")
    assert days("yesterday") == ("2026-08-02", "2026-08-03")


def test_parse_when_weeks():
    # "next week" means the next CALENDAR week, not the next seven days.
    assert days("this week") == ("2026-08-03", "2026-08-10")
    assert days("next week") == ("2026-08-10", "2026-08-17")
    assert days("last week") == ("2026-07-27", "2026-08-03")


def test_parse_when_counted_days():
    assert days("next 30 days") == ("2026-08-03", "2026-09-02")
    assert days("last 7 days") == ("2026-07-27", "2026-08-04")


def test_parse_when_months_and_iso_dates():
    assert days("this month") == ("2026-08-01", "2026-09-01")
    assert days("next month") == ("2026-09-01", "2026-10-01")
    assert days("2026-12-25") == ("2026-12-25", "2026-12-26")


def test_parse_when_weekday_names():
    assert days("friday") == ("2026-08-07", "2026-08-08")
    assert days("next friday") == ("2026-08-14", "2026-08-15")
    # Today IS Monday. "monday" should mean the next one, not today.
    assert days("monday") == ("2026-08-10", "2026-08-11")


def test_parse_when_complains_about_nonsense():
    from assistant.queries import parse_when

    with pytest.raises(ValueError):
        parse_when("sometime around the third moon", NOW)


def test_events_in_range_uses_overlap_not_containment(occurrences):
    from assistant.queries import events_in_range

    # The migration kickoff runs 13:00-14:30 on Thursday. Asking about a slice
    # in the middle of it must still find it.
    start = datetime(2026, 8, 6, 14, 0)
    end = datetime(2026, 8, 6, 14, 15)
    summaries = [o["summary"] for o in events_in_range(occurrences, start, end)]
    assert "Platform migration kickoff" in summaries


def test_find_events_shows_this_week(occurrences):
    from assistant.queries import find_events

    text = find_events(occurrences, "this week")
    assert "Daily standup" in text
    assert "Platform migration kickoff" in text
    assert "Mon 03 Aug" in text, "the answer should say which dates it used"


def test_find_events_can_filter_by_person(occurrences):
    from assistant.queries import find_events

    text = find_events(occurrences, "this week", person="priya")
    assert "1:1 with Priya" in text
    assert "Dentist" not in text


def test_find_events_says_so_when_nothing_is_scheduled(occurrences):
    from assistant.queries import find_events

    # A Sunday in December. (Christmas Day 2026 is a Friday, and the standup
    # repeats on Fridays forever - so it is NOT an empty day.)
    assert "Nothing scheduled" in find_events(occurrences, "2026-12-27")


def test_upcoming_birthdays_are_soonest_first(occurrences):
    from assistant.queries import upcoming_birthdays

    text = upcoming_birthdays(occurrences, NOW, within_days=30)
    lines = text.strip().splitlines()
    assert lines[0].startswith("Ana Ortiz")
    assert "in 4 days" in lines[0]
    assert "Marcus Bell" in text


def test_upcoming_birthdays_only_returns_birthdays(occurrences):
    from assistant.queries import upcoming_birthdays

    text = upcoming_birthdays(occurrences, NOW, within_days=30)
    assert "standup" not in text.lower()
    assert "PTO" not in text


def test_upcoming_birthdays_respects_the_window(occurrences):
    from assistant.queries import upcoming_birthdays

    text = upcoming_birthdays(occurrences, NOW, within_days=7)
    assert "Ana Ortiz" in text
    assert "Marcus Bell" not in text  # his is 16 days away


def test_find_free_time_finds_the_empty_day(occurrences):
    from assistant.queries import find_free_time

    text = find_free_time(occurrences, "this week", duration_minutes=120)
    assert "Wed 05 Aug" in text  # nothing scheduled that day


def test_find_free_time_stays_inside_working_hours(occurrences):
    from assistant.queries import find_free_time

    text = find_free_time(occurrences, "this week", duration_minutes=30)
    assert "08:00" not in text and "18:00" not in text
    assert "Sat" not in text and "Sun" not in text


def test_find_free_time_handles_overlapping_meetings(occurrences):
    from assistant.queries import find_free_time

    # Thursday is double-booked: a 13:00-14:30 kickoff overlapping a
    # 14:00-14:30 1:1. Together they block exactly 13:00-14:30, so the only
    # gaps are before and after. If you subtract each meeting separately
    # without merging them first, you can invent a gap that is not free.
    text = find_free_time(occurrences, "2026-08-06", duration_minutes=15)
    assert "09:00-13:00" in text
    assert "14:30-17:00" in text
    assert "14:00-" not in text
