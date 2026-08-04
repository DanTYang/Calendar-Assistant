"""Expand recurrence rules into dated occurrences.

A calendar file stores *rules*, not meetings. The standup is one event:

    DTSTART:20260525T093000
    RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
    EXDATE:20260805T093000

That is roughly 150 meetings a year, and next Monday's exists nowhere in the
file until it is calculated. Same for birthdays: "Ana's birthday is 7 August
2026" is never written down - the file says "starts 7 August 1993, yearly".

This is why the calendar half of the assistant cannot be answered by searching
text. The answer is not in the text.

Vocabulary used throughout the project:
  - an EVENT is what the file stores: one rule
  - an OCCURRENCE is one dated instance of it
"""

from dateutil.rrule import rrulestr

# Lists on an event are shared by reference when an occurrence is copied, so
# they are duplicated explicitly to keep occurrences independent.
LIST_FIELDS = ("attendees", "categories", "exdates")


def expand_event(event, window_start, window_end):
    """Return every occurrence of one event that overlaps [window_start, window_end).

    Recurrence rules are handled by `dateutil` rather than by hand: RRULE
    supports corners like "the last Tuesday of every other month" and getting
    all of them right is weeks of work.

    Two things worth knowing:

    - `rule.between()` rather than `list(rule)`. A weekly rule with no UNTIL is
      genuinely infinite, so materialising it never terminates.

    - The lower bound is nudged back by the event's duration, so a meeting that
      started before the window but is still running is generated and can then
      be tested for overlap.

    Occurrences keep the original's duration; the window selects them, it never
    clips them.
    """
    duration = event["end"] - event["start"]
    excluded = set(event["exdates"])

    if event["rrule"]:
        rule = rrulestr("RRULE:" + event["rrule"], dtstart=event["start"])
        starts = rule.between(window_start - duration, window_end, inc=True)
    else:
        starts = [event["start"]]

    occurrences = []
    for start in starts:
        if start in excluded:
            continue
        end = start + duration
        # Overlap, not containment: a meeting running 13:00-14:30 belongs to a
        # 14:00-14:15 window even though it did not start inside it.
        if start < window_end and end > window_start:
            occurrence = dict(event, start=start, end=end)
            for field in LIST_FIELDS:
                occurrence[field] = list(event[field])
            occurrences.append(occurrence)
    return occurrences


def expand_all(events, window_start, window_end):
    """Expand every event in the window, sorted by start time."""
    occurrences = []
    for event in events:
        occurrences.extend(expand_event(event, window_start, window_end))
    occurrences.sort(key=lambda occurrence: occurrence["start"])
    return occurrences