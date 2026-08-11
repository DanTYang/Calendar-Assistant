"""Command-line entry point.

    python -m assistant.main              # chat with the assistant
    python -m assistant.main agenda       # print a date range, no model needed
    python -m assistant.main birthdays    # print upcoming birthdays, no model needed
    python -m assistant.main cache        # save a local copy of a Google calendar

Choose where events come from with --source:

    --source file   data/sample_calendar.ics, or $CALENDAR_FILE  (default)
    --source url    a Google calendar's secret iCal address
"""

import atexit
import sys
from datetime import timedelta

import config
from assistant import agent, ics_parser, memory, queries, recurrence, search
from assistant.llm import have_api_key

# How far either side of "now" to expand recurring events. A year covers every
# question the assistant can be asked, including yearly birthdays.
HORIZON = timedelta(days=365)

SOURCES = ("file", "url", "api")


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

    if source == "api":
        from assistant import google_api
        return google_api.load_from_api()
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


def chat(occurrences, chunks, source):
    """Read questions from the terminal until the user quits."""
    print(f"Today is {config.NOW:%A %d %B %Y}.")
    if not have_api_key():
        print("No ANTHROPIC_API_KEY set - using the offline keyword model.")
    print("Ask me about your calendar. Type 'quit' to stop.\n")

    conversation = memory.Conversation()
    while True:
        try:
            question = input("Q> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question.lower() in {"quit", "exit"}:
            return
        if question:
            answer = agent.ask(question, occurrences, chunks, conversation,
                               on_tool_call=show_tool_call,
                               source=source)
            # Continuation lines are indented to sit under the text after "A> ",
            # so a multi-line answer still reads as one block.
            print("\nA> " + answer.replace("\n", "\n   ") + "\n")


def _sign_out_on_exit():
    """Leave nothing signed-in behind when the session ends."""
    from assistant import google_api
    if google_api.sign_out():
        print("\nSigned out: token revoked with Google and deleted.")
    else:
        print(f"\nToken deleted ({config.TOKEN_FILE.name}).")


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
    source_given = "--source" in args
    source = take_source(args)
    command = args[0] if args else "chat"

    if command == "cache":
        from assistant import google_calendar
        print(f"Saved a copy to {google_calendar.cache_from_url()}")
        return 0

    # Sign-in needs no calendar loaded, and reading a few events back is the
    # proof that the token works. On success this falls through to the chat
    # session below rather than returning.
    if command == "login":
        from assistant import google_api
        while True:
            try:
                account, zone = google_api.signed_in_account()
                upcoming = google_api.list_upcoming()
                break
            except google_api.AuthError as error:
                # Every AuthError message already says what to do about it.
                print(f"\nSign-in failed. {error}\n")
                try:
                    again = input("Try signing in with Google again? [y/N] ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 1
                if again.strip().lower() not in {"y", "yes"}:
                    print("Not signed in.")
                    return 1
                print()

        # The account matters more than the token path: every writing tool acts
        # on "primary", so this line is the only thing saying which calendar
        # that is.
        print(f"Signed in as {account}  (calendar timezone: {zone})")
        if zone and zone != str(config.TIMEZONE):
            print(f"  Note: this project assumes {config.TIMEZONE}. Set "
                  "CALENDAR_TIMEZONE to match, or times will be off.")
        print()
        for start, summary in upcoming:
            print(f"  {start}  {summary}")
        if not upcoming:
            print("  (no upcoming events on the primary calendar)")
        print()

        # Read back through the account just signed in to, rather than the
        # sample calendar or the feed. The token is already in hand, and unlike
        # the feed the API shows a change the moment it is made. An explicit
        # --source always wins.
        if not source_given:
            source = "api"

        # Registered rather than wrapped in try/finally so it also runs when
        # the session ends by exception, not only on a clean quit.
        if config.REVOKE_ON_EXIT:
            atexit.register(_sign_out_on_exit)

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
        chat(occurrences, chunks, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())