"""Converting Google's JSON into the project's event dictionary.

No network and no credentials: the payloads below are the shapes the API
actually returns, which is all the converter cares about.
"""

from datetime import datetime, timedelta

from conftest import NOW

TIMED = {
    "id": "standup-1",
    "status": "confirmed",
    "summary": "Daily standup",
    "location": "Zoom",
    "start": {"dateTime": "2026-08-03T09:30:00+01:00", "timeZone": "Europe/London"},
    "end": {"dateTime": "2026-08-03T09:45:00+01:00"},
    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
                   "EXDATE;TZID=Europe/London:20260805T093000"],
    "attendees": [{"email": "ana@example.com", "displayName": "Ana Ortiz"},
                  {"email": "nobody@example.com"}],
}

ALL_DAY = {
    "id": "bday-ana",
    "status": "confirmed",
    "summary": "Ana Ortiz's Birthday",
    "eventType": "birthday",
    "start": {"date": "2026-08-07"},
    "end": {"date": "2026-08-08"},
    "recurrence": ["RRULE:FREQ=YEARLY"],
}


def test_timed_events_lose_the_offset_and_keep_wall_clock_time():
    from assistant.google_calendar import google_datetime

    when, all_day = google_datetime({"dateTime": "2026-08-03T09:30:00+01:00"})
    assert when == datetime(2026, 8, 3, 9, 30)
    assert when.tzinfo is None
    assert all_day is False


def test_all_day_events_are_flagged():
    from assistant.google_calendar import google_datetime

    when, all_day = google_datetime({"date": "2026-08-07"})
    assert when == datetime(2026, 8, 7)
    assert all_day is True


def test_converted_events_match_the_parser_contract():
    from assistant.google_calendar import convert_event
    from assistant.ics_parser import new_event

    event = convert_event(TIMED)
    assert set(event) == set(new_event()), "must match the event dictionary exactly"
    assert event["uid"] == "standup-1"
    assert event["summary"] == "Daily standup"
    assert event["location"] == "Zoom"
    assert event["start"] == datetime(2026, 8, 3, 9, 30)


def test_recurrence_rules_survive_the_conversion():
    from assistant.google_calendar import convert_event

    event = convert_event(TIMED)
    assert event["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert event["exdates"] == [datetime(2026, 8, 5, 9, 30)]


def test_attendee_names_are_used_with_email_as_the_fallback():
    from assistant.google_calendar import convert_event

    # displayName is missing for anyone not in your contacts.
    assert convert_event(TIMED)["attendees"] == ["Ana Ortiz", "nobody@example.com"]


def test_birthdays_are_tagged_so_the_query_layer_recognises_them():
    from assistant.google_calendar import convert_event
    from assistant.queries import is_birthday

    assert is_birthday(convert_event(ALL_DAY))


def test_cancelled_events_are_dropped():
    from assistant.google_calendar import convert_events

    cancelled = dict(TIMED, status="cancelled")
    assert convert_events([cancelled, ALL_DAY]) == convert_events([ALL_DAY])


def test_a_missing_end_time_is_filled_in():
    from assistant.google_calendar import convert_event

    without_end = {k: v for k, v in TIMED.items() if k != "end"}
    event = convert_event(without_end)
    assert event["end"] - event["start"] == timedelta(hours=1)

    all_day_without_end = {k: v for k, v in ALL_DAY.items() if k != "end"}
    event = convert_event(all_day_without_end)
    assert event["end"] - event["start"] == timedelta(days=1)


def test_events_without_a_usable_start_are_dropped():
    from assistant.google_calendar import convert_event

    assert convert_event({"id": "broken", "summary": "no start"}) is None


def test_converted_events_work_with_the_rest_of_the_pipeline():
    """The point of the converter: nothing downstream needed changing."""
    from assistant.google_calendar import convert_events
    from assistant.queries import find_events
    from assistant.recurrence import expand_all

    events = convert_events([TIMED, ALL_DAY])
    occurrences = expand_all(events, NOW - timedelta(days=365), NOW + timedelta(days=365))

    text = find_events(occurrences, "this week")
    assert "Daily standup" in text
    assert "Ana Ortiz's Birthday" in text
    # Wednesday 5 August is excluded by the EXDATE that came from Google.
    assert "Wed 05 Aug" not in text