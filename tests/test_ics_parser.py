"""Part 1 — reading the calendar file."""

from datetime import datetime

from conftest import find


def test_unfold_lines_joins_continuation_lines():
    from assistant.ics_parser import unfold_lines

    text = "SUMMARY:hello\r\nDESCRIPTION:a very long\r\n  line that was folded\r\n"
    assert unfold_lines(text) == ["SUMMARY:hello", "DESCRIPTION:a very long line that was folded"]


def test_unfold_lines_drops_blank_lines():
    from assistant.ics_parser import unfold_lines

    assert unfold_lines("A:1\n\n\nB:2\n") == ["A:1", "B:2"]


def test_parse_line_splits_name_and_value():
    from assistant.ics_parser import parse_line

    name, params, value = parse_line("SUMMARY:Team lunch")
    assert name == "SUMMARY"
    assert params == {}
    assert value == "Team lunch"


def test_parse_line_splits_on_the_first_colon_only():
    from assistant.ics_parser import parse_line

    # The value contains a colon (mailto:). Splitting on every colon breaks this.
    name, params, value = parse_line("ATTENDEE;CN=Ana Ortiz:mailto:ana@example.com")
    assert name == "ATTENDEE"
    assert params == {"CN": "Ana Ortiz"}
    assert value == "mailto:ana@example.com"


def test_parse_datetime_handles_both_formats():
    from assistant.ics_parser import parse_datetime

    assert parse_datetime("20260803T093000") == datetime(2026, 8, 3, 9, 30)
    assert parse_datetime("20260803") == datetime(2026, 8, 3)
    assert parse_datetime("20260803T093000Z") == datetime(2026, 8, 3, 9, 30)


def test_finds_every_event(events):
    assert len(events) == 17, f"expected 17 events, got {len(events)}"


def test_reads_the_basic_fields(events):
    dentist = find(events, "dentist")
    assert dentist["summary"] == "Dentist"
    assert dentist["start"] == datetime(2026, 8, 4, 8, 0)
    assert dentist["end"] == datetime(2026, 8, 4, 9, 0)
    assert dentist["location"] == "Dr Aliyev, 2nd Ave"
    assert dentist["all_day"] is False


def test_reads_attendee_names(events):
    standup = find(events, "standup")
    assert "Priya Raghavan" in standup["attendees"]
    assert len(standup["attendees"]) == 3


def test_reads_repeat_rules_and_skipped_dates(events):
    standup = find(events, "standup")
    assert standup["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert datetime(2026, 8, 5, 9, 30) in standup["exdates"]


def test_all_day_events_are_flagged(events):
    birthday = find(events, "bday-ana")
    assert birthday["all_day"] is True
    assert birthday["start"] == datetime(1993, 8, 7)
    # DTEND on an all-day event is the day AFTER it ends. A one-day event on the
    # 7th ends on the 8th. This is a real rule in the file format, not a typo.
    assert birthday["end"] == datetime(1993, 8, 8)
    assert "Birthday" in birthday["categories"]


def test_long_descriptions_are_not_truncated(events):
    """If you skipped unfolding, this description will be cut off partway."""
    vendor = find(events, "vendor")
    assert "annual tier" in vendor["description"], "the description looks truncated"
