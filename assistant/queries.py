"""Answer calendar questions with ordinary Python - no model involved.

Look at what people actually ask a calendar:

    "what's on Tuesday?"            -> filter a list by date
    "whose birthday is coming up?"  -> filter, sort, take the soonest
    "when am I free for an hour?"   -> subtract busy times from working hours

Four questions, four ordinary list operations. None of them needs a language
model and none of them needs semantic search. Ranking events by how much they
resemble the word "birthday" returns things that sound birthday-ish, in no
particular order; filtering and sorting returns the right answer.

Every function here returns a *string*, because in `agent` these strings are
what the model reads. A neat fixed-width line costs a fraction of the tokens a
JSON dump would and is easier for a model to quote back accurately.
"""

import re
from datetime import datetime, timedelta

import config

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}

WEEK = timedelta(days=7)

# Returned to the caller - and in the agent loop, to the model - when a date
# phrase cannot be resolved. Listing what *does* work lets the model retry
# correctly on its next step instead of the conversation dying.
SUPPORTED_PHRASES = (
    "Unsupported date phrase. Supported formats: today, tomorrow, yesterday, "
    "this/next/last week, this/next/last month, next N days, last N days, "
    "YYYY-MM-DD, weekday names, and 'next <weekday>'."
)

BIRTHDAY_SUFFIX = "'s Birthday"


def _one_day(midnight):
    """The half-open range covering a single calendar day."""
    return midnight, midnight + timedelta(days=1)


def _next_month(first_of_month):
    """First day of the following month. 32 days always overshoots into it."""
    return (first_of_month + timedelta(days=32)).replace(day=1)


def _previous_month(first_of_month):
    return (first_of_month - timedelta(days=1)).replace(day=1)


def parse_when(when, now):
    """Resolve a date phrase into a half-open (start, end) pair of datetimes.

    This is why the assistant can be trusted with dates. Models are unreliable
    at date arithmetic and wrong confidently, so the model chooses the *phrase*
    and this function decides what the phrase means.

    Two conventions:

    - Ranges are half-open, [start, end). The end is the first moment not
      included, so an event at exactly midnight belongs to one day rather than
      two.

    - "next week" means the next calendar week, Monday to Sunday, not the next
      seven days. People mean the first one.

    Raises ValueError with `SUPPORTED_PHRASES` for anything unrecognised.
    """
    text = " ".join(when.strip().lower().split())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight - timedelta(days=midnight.weekday())
    month_start = midnight.replace(day=1)

    if text == "today":
        return _one_day(midnight)
    if text == "tomorrow":
        return _one_day(midnight + timedelta(days=1))
    if text == "yesterday":
        return _one_day(midnight - timedelta(days=1))

    if text == "this week":
        return week_start, week_start + WEEK
    if text == "next week":
        return week_start + WEEK, week_start + 2 * WEEK
    if text == "last week":
        return week_start - WEEK, week_start

    if text == "this month":
        return month_start, _next_month(month_start)
    if text == "next month":
        following = _next_month(month_start)
        return following, _next_month(following)
    if text == "last month":
        return _previous_month(month_start), month_start

    match = re.match(r"^next (\d+) days?$", text)
    if match:
        return midnight, midnight + timedelta(days=int(match.group(1)))

    match = re.match(r"^last (\d+) days?$", text)
    if match:
        # The window runs up to the end of today, so "last 7 days" includes today.
        return midnight - timedelta(days=int(match.group(1))), midnight + timedelta(days=1)

    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        try:
            return _one_day(datetime.strptime(text, "%Y-%m-%d"))
        except ValueError:
            pass  # looked like a date, was not one - fall through to the error

    match = re.match(r"^next (\w+)$", text)
    if match and match.group(1) in WEEKDAYS:
        ahead = (WEEKDAYS[match.group(1)] - midnight.weekday()) % 7 + 7
        return _one_day(midnight + timedelta(days=ahead))

    if text in WEEKDAYS:
        # A weekday name that lands on today means the next one, not today.
        ahead = (WEEKDAYS[text] - midnight.weekday()) % 7 or 7
        return _one_day(midnight + timedelta(days=ahead))

    raise ValueError(SUPPORTED_PHRASES)


def events_in_range(occurrences, start, end):
    """Occurrences overlapping the half-open window [start, end).

    Overlap, not containment: a meeting running 13:00-14:30 must appear when
    you ask about 14:00-14:15, even though it did not begin inside the window.
    """
    return [occ for occ in occurrences
            if occ["end"] > start and occ["start"] < end]


def format_event(occurrence):
    """Render one occurrence as a single aligned line.

        Thu 06 Aug  13:00-14:30  Platform migration kickoff  @ Room 2A  [Marcus Bell]
        Fri 07 Aug      all day  Ana Ortiz's Birthday

    Location and attendees are omitted when absent rather than left blank.
    """
    if occurrence["all_day"]:
        span = "all day"
    else:
        span = f"{occurrence['start']:%H:%M}-{occurrence['end']:%H:%M}"

    line = f"{occurrence['start']:%a %d %b}  {span:>11}  {occurrence['summary']}"
    if occurrence["location"]:
        line += f"  @ {occurrence['location']}"
    if occurrence["attendees"]:
        line += f"  [{', '.join(occurrence['attendees'])}]"
    return line


def find_events(occurrences, when, person="", contains=""):
    """Answer "what's on <when>?", optionally filtered by attendee or text.

    The header always names the resolved dates:

        this week (Mon 03 Aug to Sun 09 Aug)

    If the caller misunderstood which week was meant, that line is how they
    find out. An answer with no dates in it hides its own mistakes.
    """
    start, end = parse_when(when, config.NOW)
    found = events_in_range(occurrences, start, end)

    if person:
        needle = person.lower()
        found = [occ for occ in found
                 if any(needle in attendee.lower() for attendee in occ["attendees"])]

    if contains:
        needle = contains.lower()
        found = [occ for occ in found
                 if needle in occ["summary"].lower()
                 or needle in occ["description"].lower()]

    header = f"{when} ({start:%a %d %b} to {end - timedelta(days=1):%a %d %b})"
    if not found:
        return f"{header}\nNothing scheduled."
    return "\n".join([header] + [format_event(occ) for occ in found])


def is_birthday(occurrence):
    """True if this occurrence is somebody's birthday.

    Deliberately strict. Matching "birthday" anywhere in the summary would pull
    in "Birthday party planning sync" - a work meeting - and a wrong answer
    people can see is worse than a missing one.
    """
    return ("Birthday" in occurrence["categories"]
            or occurrence["summary"].endswith(BIRTHDAY_SUFFIX))


def person_from_birthday(summary):
    """"Ana Ortiz's Birthday" -> "Ana Ortiz". Anything else is returned as-is."""
    return summary.removesuffix(BIRTHDAY_SUFFIX)


def _days_away(days):
    """Human phrasing for a gap in days. "in 0 days" reads like a bug."""
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def upcoming_birthdays(occurrences, now, within_days=45):
    """Answer "whose birthday is coming up?".

        Ana Ortiz - Friday 07 August (in 4 days)

    Note what this does *not* do: it never searches text. The date it reports
    was computed by `recurrence` from a rule; it appears nowhere in the
    calendar file, so there is nothing for a text search to match against. And
    "coming up" is a sort by start time, not a similarity ranking.
    """
    found = []
    for occurrence in occurrences:
        if not is_birthday(occurrence):
            continue
        away = (occurrence["start"].date() - now.date()).days
        if 0 <= away <= within_days:
            found.append((occurrence["start"], person_from_birthday(occurrence["summary"]), away))
    found.sort()

    lines, seen = [], set()
    for start, person, away in found:
        # A yearly rule can produce two occurrences inside a long window, and
        # "Ana's birthday is in 4 days, and also in 369 days" is not an answer.
        if person in seen:
            continue
        seen.add(person)
        lines.append(f"{person} - {start:%A %d %B} ({_days_away(away)})")

    if not lines:
        return f"No birthdays in the next {within_days} {'day' if within_days == 1 else 'days'}."
    return "\n".join(lines)


def _merge(blocks):
    """Collapse overlapping [start, end] intervals into disjoint ones.

    Real calendars are double-booked - a 13:00-14:30 kickoff alongside a
    14:00-14:30 one-to-one. Subtracting those one at a time reports free time
    that is not free, so overlapping busy blocks are merged first.
    """
    merged = []
    for block in sorted(blocks):
        if merged and block[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], block[1])
        else:
            merged.append(list(block))
    return merged


def _free_gaps(busy, work_start, work_end, minimum):
    """Gaps of at least `minimum` between merged busy blocks and the workday."""
    gaps, cursor = [], work_start
    for block_start, block_end in busy:
        block_start = max(block_start, work_start)
        block_end = min(block_end, work_end)
        if cursor < block_start and block_start - cursor >= minimum:
            gaps.append((cursor, block_start))
        cursor = max(cursor, block_end)
    if cursor < work_end and work_end - cursor >= minimum:
        gaps.append((cursor, work_end))
    return gaps


def find_free_time(occurrences, when, duration_minutes=30):
    """Answer "when am I free?" for each weekday in the range.

    All-day events are ignored: a birthday does not stop you booking a meeting.
    """
    start, end = parse_when(when, config.NOW)
    minimum = timedelta(minutes=duration_minutes)

    busy = _merge([occurrence["start"], occurrence["end"]]
                  for occurrence in events_in_range(occurrences, start, end)
                  if not occurrence["all_day"])

    lines = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        if day.weekday() < 5:  # weekdays only
            next_day = day + timedelta(days=1)
            gaps = _free_gaps(
                [block for block in busy if block[0] < next_day and block[1] > day],
                day.replace(hour=config.WORK_START_HOUR),
                day.replace(hour=config.WORK_END_HOUR),
                minimum,
            )
            if gaps:
                spans = ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in gaps)
                lines.append(f"{day:%a %d %b}: {spans}")
        day += timedelta(days=1)

    if not lines:
        return f"Nothing free between {start:%a %d %b} and {end - timedelta(days=1):%a %d %b}."
    return "\n".join(lines)