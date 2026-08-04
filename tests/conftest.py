import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# Always the pinned demo date, never config.NOW - otherwise setting
# CALENDAR_NOW=now in your shell would silently break the suite.
NOW = config.DEMO_NOW  # Monday 3 August 2026, 09:00

HORIZON = timedelta(days=365)


@pytest.fixture
def calendar_text():
    return config.CALENDAR_FILE.read_text(encoding="utf-8")


@pytest.fixture
def events():
    from assistant import ics_parser
    return ics_parser.load_calendar(config.CALENDAR_FILE)


@pytest.fixture
def occurrences(events):
    from assistant import recurrence
    return recurrence.expand_all(events, NOW - HORIZON, NOW + HORIZON)


@pytest.fixture
def chunks():
    from assistant import search
    return search.build_chunks(search.load_notes(config.NOTES_FOLDER))


def find(events, uid):
    """Look up one event by UID, with a useful message when it is missing."""
    for event in events:
        if event["uid"] == uid:
            return event
    raise AssertionError(f"no event with uid {uid!r} - did parse_events skip it?")