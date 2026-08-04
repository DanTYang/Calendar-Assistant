"""Command-line entry point.

    python -m assistant.main              # chat with the assistant
    python -m assistant.main agenda       # print a date range, no model needed
    python -m assistant.main birthdays    # print upcoming birthdays, no model needed
    python -m assistant.main cache        # save a local copy of a Google calendar

Choose where events come from with --source:

    --source file   data/sample_calendar.ics, or $CALENDAR_FILE  (default)
    --source url    a Google calendar's secret iCal address
"""

import sys
from datetime import timedelta

import config
from assistant import agent, ics_parser, memory, queries, recurrence, search
from assistant.llm import have_api_key

# How far either side of "now" to expand recurring events. A year covers every
# question the assistant can be asked, including yearly birthdays.
HORIZON = timedelta(days=365)

SOURCES = ("file", "url")


def load_events(source="file"):
    """Load events from the chosen source.

    Every branch returns the same event dictionary, so nothing below this
    function knows or cares which one ran.
    """
    if source == "file":
        return ics_parser.load_calendar(config.CALENDAR_FILE)

    if source == "url":
        from assistant import google_calendar
        return google_calendar.load_from_url()
    raise ValueError(f"unknown source {source!r}, expected one of {SOURCES}")


def load_everything(source="file"):
    """Load the calendar, expand recurrences, and index the notes."""
    events = load_events(source)
    occurrences = recurrence.expand_all(events, config.NOW - HORIZON, config.NOW + HORIZON)
    chunks = search.build_chunks(search.load_notes(config.NOTES_FOLDER))
    return events, occurrences, chunks


def show_tool_call(name, args):
    """Echo the model's tool choice - the quickest way to spot a description
    that is steering it wrong."""
    print(f"   (used {name} with {args})")


def chat(occurrences, chunks):
    """Read questions from the terminal until the user quits."""
    print(f"Today is {config.NOW:%A %d %B %Y}.")
    if not have_api_key():
        print("No ANTHROPIC_API_KEY set - using the offline keyword model.")
    print("Ask me about your calendar. Type 'quit' to stop.\n")

    conversation = memory.Conversation()
    while True:
        try:
            question = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question.lower() in {"quit", "exit"}:
            return
        if question:
            answer = agent.ask(question, occurrences, chunks, conversation,
                               on_tool_call=show_tool_call)
            print("\n" + answer + "\n")


def take_source(args):
    """Pull `--source X` out of the argument list, leaving the command behind."""
    if "--source" not in args:
        return "file"
    index = args.index("--source")
    source = args[index + 1] if len(args) > index + 1 else "file"
    del args[index:index + 2]
    return source


def main():
    args = sys.argv[1:]
    source = take_source(args)
    command = args[0] if args else "chat"

    if command == "cache":
        from assistant import google_calendar
        print(f"Saved a copy to {google_calendar.cache_from_url()}")
        return 0

    try:
        events, occurrences, chunks = load_everything(source)
    except Exception as error:
        print(f"Could not load the calendar from {source!r}: "
              f"{type(error).__name__}: {error}")
        return 1

    print(f"Loaded {len(events)} events -> {len(occurrences)} dated occurrences, "
          f"{len(chunks)} note chunks.  (source: {source})\n")

    if command == "agenda":
        print(queries.find_events(occurrences, " ".join(args[1:]) or "this week"))
    elif command == "birthdays":
        print(queries.upcoming_birthdays(occurrences, config.NOW))
    else:
        chat(occurrences, chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())