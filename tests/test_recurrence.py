"""Part 2 — turning repeating events into real dates."""

from datetime import datetime, timedelta

from conftest import NOW, find


def week():
    monday = datetime(2026, 8, 3)
    return monday, monday + timedelta(days=7)


def test_a_one_off_event_produces_exactly_one_occurrence(events):
    from assistant.recurrence import expand_event

    start, end = week()
    got = expand_event(find(events, "dentist"), start, end)
    assert len(got) == 1
    assert got[0]["start"] == datetime(2026, 8, 4, 8, 0)


def test_a_weekly_event_repeats_on_the_right_days(events):
    from assistant.recurrence import expand_event

    start, end = week()
    got = expand_event(find(events, "standup"), start, end)
    weekdays = sorted({o["start"].weekday() for o in got})
    assert weekdays == [0, 4], "standup should land on Monday and Friday this week"
    assert all(o["start"].hour == 9 and o["start"].minute == 30 for o in got)


def test_skipped_dates_are_removed(events):
    from assistant.recurrence import expand_event

    start, end = week()
    dates = {o["start"].date() for o in expand_event(find(events, "standup"), start, end)}
    # Wednesday 5 August is in the EXDATE list, so it must not appear.
    assert datetime(2026, 8, 5).date() not in dates


def test_occurrences_keep_the_original_length(events):
    from assistant.recurrence import expand_event

    start, end = week()
    got = expand_event(find(events, "standup"), start, end)
    assert all(o["end"] - o["start"] == timedelta(minutes=15) for o in got)


def test_a_yearly_birthday_lands_in_the_current_year(events):
    from assistant.recurrence import expand_event

    got = expand_event(find(events, "bday-ana"), datetime(2026, 1, 1), datetime(2027, 1, 1))
    assert len(got) == 1
    assert got[0]["start"] == datetime(2026, 8, 7)


def test_events_outside_the_window_are_left_out(events):
    from assistant.recurrence import expand_event

    quiet = datetime(2026, 12, 1), datetime(2026, 12, 8)
    assert expand_event(find(events, "dentist"), *quiet) == []


def test_expand_all_returns_everything_in_time_order(events):
    from assistant.recurrence import expand_all

    got = expand_all(events, NOW - timedelta(days=365), NOW + timedelta(days=365))
    assert len(got) > 100
    starts = [o["start"] for o in got]
    assert starts == sorted(starts), "expand_all should return occurrences sorted by start time"
