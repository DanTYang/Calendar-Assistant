"""Parse an iCalendar (.ics) file into plain Python dictionaries.

An .ics file is line-oriented text. Every line is NAME;PARAM=VALUE:VALUE, and
events are wrapped in BEGIN:VEVENT / END:VEVENT:

    BEGIN:VEVENT
    UID:dentist
    DTSTART:20260804T080000
    DTEND:20260804T090000
    SUMMARY:Dentist
    LOCATION:Dr Aliyev, 2nd Ave
    END:VEVENT

Each event becomes one dictionary with a fixed set of keys (see `new_event`).
That dictionary is the contract every other module depends on - anything able
to produce it can be plugged in as a calendar source without touching the code
downstream.

Simplification: timezones are ignored. Every datetime is naive and treated as
wall-clock time, so a UTC "Z" suffix or a numeric offset is discarded rather
than converted.
"""

from datetime import datetime, timedelta
from pathlib import Path

# Default durations for an event whose DTEND is missing.
DEFAULT_DURATION = timedelta(hours=1)
DEFAULT_ALL_DAY_DURATION = timedelta(days=1)

# Backslash escapes that may appear inside a TEXT value.
TEXT_ESCAPES = {"\\": "\\", ";": ";", ",": ",", "n": "\n", "N": "\n"}


def unfold_lines(text):
    """Split text into logical lines, rejoining ones the format split in half.

    No .ics line may exceed ~75 characters, so longer ones are folded across
    several physical lines, each continuation beginning with a single space:

        DESCRIPTION:Reviewing the Northwind renewal before it expires on 30 Sep
         tember. Legal flagged the auto-renewal clause.

    That is one logical line. Reading the file line by line without rejoining
    silently truncates every long description.

    Returns a list of strings with blank lines removed.
    """
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return [line for line in lines if line]


def _find_outside_quotes(text, target):
    """Index of the first `target` character not inside a quoted string, or -1.

    Parameter values may be quoted precisely so they can contain the delimiters
    (`ATTENDEE;CN="Ortiz: Ana":mailto:...`), so a naive search finds the wrong
    colon.
    """
    in_quotes = False
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
        elif char == target and not in_quotes:
            return index
    return -1


def _split_outside_quotes(text, separator):
    """Split on `separator`, ignoring separators inside quoted strings."""
    pieces, start, in_quotes = [], 0, False
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
        elif char == separator and not in_quotes:
            pieces.append(text[start:index])
            start = index + 1
    pieces.append(text[start:])
    return pieces


def split_escaped(value, separator=","):
    """Split a list-valued property on unescaped separators only.

    `CATEGORIES:Personal\\, urgent,Birthday` is two categories, not three - the
    first comma is escaped and belongs to the text.
    """
    pieces, current, index = [], [], 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            current.append(value[index:index + 2])
            index += 2
            continue
        if char == separator:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    pieces.append("".join(current))
    return pieces


def unescape_text(value):
    r"""Resolve the backslash escapes the format requires inside TEXT values.

    `Lunch\; then gym` -> `Lunch; then gym`
    """
    out, index = [], 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            following = value[index + 1]
            out.append(TEXT_ESCAPES.get(following, following))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def parse_line(line):
    """Split one line into (NAME, params, value), or None if it has no colon.

        "SUMMARY:Dentist"
            -> ("SUMMARY", {}, "Dentist")
        "DTSTART;VALUE=DATE:20260807"
            -> ("DTSTART", {"VALUE": "DATE"}, "20260807")
        "ATTENDEE;CN=Ana Ortiz:mailto:ana@example.com"
            -> ("ATTENDEE", {"CN": "Ana Ortiz"}, "mailto:ana@example.com")

    Only the first colon separates the name from the value - the value itself
    frequently contains more of them, as `mailto:` above shows. Names and
    parameter keys are case-insensitive in the format, so both are upper-cased
    here and callers can compare against constants.
    """
    colon = _find_outside_quotes(line, ":")
    if colon == -1:
        return None

    head, value = line[:colon], line[colon + 1:]
    pieces = _split_outside_quotes(head, ";")

    params = {}
    for piece in pieces[1:]:
        key, separator, raw = piece.partition("=")
        if separator:
            params[key.strip().upper()] = raw.strip().strip('"')

    return pieces[0].strip().upper(), params, value


def parse_datetime(value):
    """Turn an iCalendar timestamp into a naive datetime.

        "20260803T093000"   -> datetime(2026, 8, 3, 9, 30)
        "20260803"          -> datetime(2026, 8, 3)          a whole day
        "20260803T093000Z"  -> datetime(2026, 8, 3, 9, 30)   UTC marker dropped
    """
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1]
    elif len(value) > 15 and value[-5] in "+-":
        value = value[:-5]

    if len(value) == 8:
        return datetime.strptime(value, "%Y%m%d")
    if len(value) == 15:
        return datetime.strptime(value, "%Y%m%dT%H%M%S")
    raise ValueError(f"unrecognised timestamp {value!r} (length {len(value)})")

def strip_until_marker(rule):
    """Drop the UTC marker from an RRULE's UNTIL, matching parse_datetime.

    dateutil compares UNTIL's timezone-awareness against DTSTART's. DTSTART is
    naive here, so an UNTIL ending in Z raises ValueError before a single
    occurrence is generated.
    """
    parts = []
    for part in rule.split(";"):
        if part.upper().startswith("UNTIL=") and part.endswith("Z"):
            part = part[:-1]
        parts.append(part)
    return ";".join(parts)


def new_event():
    """A blank event, so no downstream code has to guard against missing keys."""
    return {
        "uid": "", "summary": "", "description": "", "location": "",
        "start": None, "end": None, "all_day": False,
        "rrule": "", "exdates": [], "attendees": [], "categories": [],
    }


def _finish(event):
    """Fill in an end time for events that omit DTEND, which is legal."""
    if event["end"] is None and event["start"] is not None:
        span = DEFAULT_ALL_DAY_DURATION if event["all_day"] else DEFAULT_DURATION
        event["end"] = event["start"] + span
    return event


def parse_events(text):
    """Turn the text of an .ics file into a list of event dictionaries.

    Events without a UID or a start are skipped as malformed. Note that an
    event here is a *rule*, not a meeting: a weekly standup is one event whose
    `rrule` expands into hundreds of occurrences (see `recurrence`).
    """
    events = []
    event = None

    for line in unfold_lines(text):
        if line == "BEGIN:VEVENT":
            event = new_event()
            continue
        if line == "END:VEVENT":
            if event and event["uid"] and event["start"]:
                events.append(_finish(event))
            event = None
            continue
        if event is None:
            continue

        parsed = parse_line(line)
        if parsed is None:
            continue
        name, params, value = parsed

        if name == "UID":
            event["uid"] = value
        elif name == "SUMMARY":
            event["summary"] = unescape_text(value)
        elif name == "DESCRIPTION":
            event["description"] = unescape_text(value)
        elif name == "LOCATION":
            event["location"] = unescape_text(value)
        elif name == "DTSTART":
            event["start"] = parse_datetime(value)
            event["all_day"] = params.get("VALUE") == "DATE" or len(value.strip()) == 8
        elif name == "DTEND":
            event["end"] = parse_datetime(value)
        elif name == "RRULE":
            event["rrule"] = strip_until_marker(value)
        elif name == "EXDATE":
            event["exdates"].extend(parse_datetime(d) for d in split_escaped(value))
        elif name == "CATEGORIES":
            event["categories"].extend(
                unescape_text(c).strip() for c in split_escaped(value))
        elif name == "ATTENDEE" and "CN" in params:
            event["attendees"].append(params["CN"])

    return events


def load_calendar(path):
    """Read an .ics file from disk and parse it."""
    return parse_events(Path(path).read_text(encoding="utf-8"))