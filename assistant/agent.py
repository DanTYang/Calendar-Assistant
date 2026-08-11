"""The tool-calling loop: let a language model call the query functions.

The model cannot see the calendar and cannot run code. What it can do is read a
list of function descriptions and say "call find_events with when='next week'".
This module runs that call, sends the result back, and the model turns it into
a sentence.

    "what's on next week?"
        -> model asks for find_events(when="next week")
        -> we run it and return the text
        -> model writes the answer

Sometimes it takes more than one round - the model searches the notes, sees an
unfamiliar name, and looks that person up. So this is a loop, and because it is
a loop it needs a limit.
"""

import config
from assistant import memory, queries, search
from assistant.llm import call_model

# A model that keeps calling the same tool is a real failure mode, not a
# hypothetical one, and every round costs a request.
MAX_STEPS = 5

# Every tool that changes the calendar follows the same two-call contract, so
# it is written once. Repeating it three times invited them to drift, and they
# did: delete_event alone picked up an extra confirmation from the model, which
# asked before calling the tool at all - approving a guess rather than the
# resolved event, and teaching the user to click through prompts.
TWO_PHASE = (
    "This tool takes TWO calls. Make the first call immediately, WITHOUT "
    "confirm: it changes nothing and returns exactly what would happen. Do not "
    "ask the user before that first call - its output is what you show them, "
    "and it is more accurate than a description written from the request. "
    "Then call again with the same arguments plus confirm=true, and only once "
    "the user has agreed. Never pass confirm=true on the first call, and never "
    "treat the original request as agreement: asking for something is not "
    "approving the specific details worked out for it. "
)

# The `confirm` field is identical across the writing tools too.
CONFIRM_FIELD = {
    "type": "boolean",
    "description": (
        "Leave this out on the first call. Set it to true only after the user "
        "has seen what the first call returned and agreed to it."
    ),
}

# Each entry describes one function in words the model can act on. `description`
# is not a comment for humans - it is the only thing the model uses to decide
# whether this is the right tool, so vagueness here shows up as wrong tool
# choices at runtime.
TOOLS = [
    {
        "name": "create_event",
        "description": (
            "Add a new event to the calendar. " + TWO_PHASE +
            "'when' must name a single day, such as 'tomorrow', 'friday', or "
            "'2026-08-15'; a range like 'next week' is refused because it does "
            "not say which day. Omit start_time for an all-day event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Title of the event, such as 'Lunch with Priya'.",
                },
                "when": {
                    "type": "string",
                    "description": (
                        "The single day the event falls on: 'tomorrow', "
                        "'friday', 'next friday', or a date like '2026-08-15'."
                    ),
                },
                "start_time": {
                    "type": "string",
                    "description": (
                        "Start time in 24-hour HH:MM, such as '09:00' or "
                        "'14:30'. Omit entirely for an all-day event."
                    ),
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "How long the event lasts. Defaults to 60.",
                },
                "location": {
                    "type": "string",
                    "description": "Optional location.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer notes for the event.",
                },
                "confirm": CONFIRM_FIELD,
            },
            "required": ["summary", "when"],
        },
    },
    {
        "name": "update_event",
        "description": (
            "Change an existing event - its day, time, length, title, or "
            "location. " + TWO_PHASE +
            "Identify the event with summary and when, exactly as for "
            "delete_event. Then give only the fields that should change - "
            "anything you leave out keeps its current value, so moving an "
            "event to Friday keeps its time, and changing its time keeps its "
            "day. A repeating event needs scope; see that field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Words from the title of the event to change.",
                },
                "when": {
                    "type": "string",
                    "description": "The day or range the event currently falls in.",
                },
                "new_when": {
                    "type": "string",
                    "description": (
                        "Move it to this day - a single day such as 'friday' "
                        "or '2026-08-20'. Omit to keep the current day."
                    ),
                },
                "new_start_time": {
                    "type": "string",
                    "description": (
                        "New start time in 24-hour HH:MM. Omit to keep the "
                        "current time."
                    ),
                },
                "new_duration_minutes": {
                    "type": "integer",
                    "description": "New length in minutes. Omit to keep the current length.",
                },
                "new_summary": {
                    "type": "string",
                    "description": "New title. Omit to keep the current one.",
                },
                "new_location": {
                    "type": "string",
                    "description": "New location. Omit to keep the current one.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["this", "following", "series"],
                    "description": (
                        "Required only when the event repeats, and never "
                        "guessed: 'this' affects only the occurrence on the "
                        "day given, 'following' affects that one and every "
                        "later one, and 'series' affects all of them. Ask the "
                        "user which they mean before choosing."
                    ),
                },
                "confirm": CONFIRM_FIELD,
            },
            "required": ["summary", "when"],
        },
    },
    {
        "name": "delete_event",
        "description": (
            "Remove an event from the calendar. Deleting cannot be undone. "
            + TWO_PHASE +
            "Identify the event by its title and the day it falls on. If "
            "several events match, nothing is deleted and you must ask the "
            "user which one they mean. A repeating event needs scope; see that field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Words from the event's title. Matching is partial and "
                        "case-insensitive, so 'dentist' finds 'Dentist "
                        "appointment'."
                    ),
                },
                "when": {
                    "type": "string",
                    "description": (
                        "The day or range the event falls in: 'tomorrow', "
                        "'friday', 'next week', or a date like '2026-08-15'."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["this", "following", "series"],
                    "description": (
                        "Required only when the event repeats, and never "
                        "guessed: 'this' affects only the occurrence on the "
                        "day given, 'following' affects that one and every "
                        "later one, and 'series' affects all of them. Ask the "
                        "user which they mean before choosing."
                    ),
                },
                "confirm": CONFIRM_FIELD,
            },
            "required": ["summary", "when"],
        },
    },
    {
        "name": "directions",
        "description": (
            "Get Google Maps directions links for events that have somewhere "
            "to travel to. Use this when the user asks how to get somewhere, "
            "how far away an event is, or when to set off - and offer it "
            "yourself when an event is somewhere they would have to travel. "
            "Call it directly. Do NOT search with find_events first: this tool "
            "does its own looking, and its 'summary' matches the location as "
            "well as the title, so it finds an event by where it is held when "
            "find_events cannot. "
            "Events that are online, in a meeting room, or have no location "
            "are reported as such rather than skipped. This returns a link, "
            "not a travel time: do not state durations it did not give you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "Date range to look in, such as 'tomorrow' or 'friday'.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "Optional words from the event's title or its "
                        "location, to pick one out of the range - 'Javits' "
                        "finds an event held there whatever it is called."
                    ),
                },
                "origin": {
                    "type": "string",
                    "description": (
                        "Where the journey starts. Omit unless the user says - "
                        "a link with no origin starts from wherever they are."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["driving", "transit", "walking", "bicycling"],
                    "description": "How they are travelling, if they said.",
                },
            },
            "required": ["when"],
        },
    },
    {
        "name": "find_events",
        "description": (
            "List calendar events in a date range. Use this for any question "
            "about what is scheduled, meetings, appointments, or events. "
            "The 'when' field accepts phrases like 'today', 'tomorrow', "
            "'yesterday', 'this week', 'next week', 'last week', "
            "'this month', 'next month', 'last month', 'next 30 days', "
            "'last 7 days', specific dates like '2026-08-15', and weekday "
            "names like 'friday' or 'next friday'. Use 'person' to filter "
            "events where an attendee name appears. Use 'contains' to search "
            "event titles or descriptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "Date range to search, such as 'today', 'next week', or '2026-08-15'.",
                },
                "person": {
                    "type": "string",
                    "description": "Optional attendee name to filter events by.",
                },
                "contains": {
                    "type": "string",
                    "description": "Optional text to search for in event titles or descriptions.",
                },
            },
            "required": ["when"],
        },
    },
    {
        "name": "upcoming_birthdays",
        "description": (
            "Find upcoming birthdays within a number of days. Use this instead "
            "of searching event titles for birthdays. This tool handles recurring "
            "birthday events correctly and returns the person's name and how soon "
            "their birthday occurs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "within_days": {
                    "type": "integer",
                    "description": "Number of days to look ahead for upcoming birthdays.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_free_time",
        "description": (
            "Find available free time slots during working hours. Use this when "
            "the user asks when they are free, available, or can schedule a "
            "meeting. The 'when' field accepts the same date phrases as "
            "find_events. Use duration_minutes to specify the minimum free slot "
            "length required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": "Date range to check for availability, such as 'today' or 'next week'.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Minimum length of a free slot in minutes.",
                },
            },
            "required": ["when"],
        },
    },
    {
        "name": "search_notes",
        "description": (
            "Search saved meeting notes for what was said, decided or agreed. "
            "Use this for questions about discussions, decisions and owners "
            "rather than about the schedule itself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in saved notes.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a fact for future conversations. Use this only when the user "
            "explicitly asks to remember something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or information to remember.",
                },
            },
            "required": ["fact"],
        },
    },
]

SYSTEM_PROMPT = """\
You are a calendar assistant. Today is {today}, and the time is {time}.

Answer using the tools. Never guess at dates or invent events.

Pass date phrases such as "next week" or "friday" straight through to the
tools exactly as the user said them. Do not work out the dates yourself - the
tools resolve them correctly and are the authority on what a phrase means.

Choosing a tool: birthdays -> upcoming_birthdays; what was said, decided or
agreed -> search_notes; anything about the schedule -> find_events; when
someone is free -> find_free_time.

When the user asks for a number of events and the range you tried holds fewer,
widen it and search again instead of asking permission to look further: "next
30 days", then "next 90 days", then "next 365 days". Stop widening once you
have enough, or once a year has been searched - then answer with what you
found and say how far you looked. The same goes for any search that comes back
emptier than the question implies: try a wider range before reporting nothing.

When an event is somewhere the user would have to travel to, offer directions
rather than waiting to be asked - one short question after the answer, not a
paragraph. Do not offer for anything online or in a meeting room. The
directions tool returns a link and no travel time, so never state how long a
journey takes.

How to talk:

Follow the thread. "it", "that one", "the same day", "move it instead" refer to
whatever was just discussed - resolve them from the conversation rather than
asking again. Never re-ask something the user has already settled: if they have
said which event, or which day, that answer still stands later in the same
conversation.

Offer the obvious next step as one short question, never a menu of options.
Directions after an event they have to travel to; the next free slot after
telling them a day is full; the rest of the week after a single day. When there
is no obvious next step, stop - a trailing question on every answer is worse
than none.

Ask before acting only where the answers differ materially: which of two
events, or one occurrence against a whole series. Everywhere else make the
sensible choice and say which you made, rather than handing the decision back.

When someone mentions a standing preference - where they usually work, a time
of day that suits them, what they call something - save it with remember_fact.
It is only worth saving if it would still be true next week.

Keep answers short and lead with the dates and times."""


# The tools that change a calendar, and the only source able to reach one.
WRITING_TOOLS = {"create_event", "update_event", "delete_event"}
WRITABLE_SOURCE = "api"


def tools_for(source):
    """The tools worth offering when reading from `source`.

    Writing always acts on the signed-in account's primary calendar, so a
    session reading a local file or the cached feed could create a real event
    on a calendar it is not showing. Withholding the tools is better than
    refusing the call: a tool the model cannot see is one it cannot misuse, and
    it never has to explain a refusal it did not expect.
    """
    if source == WRITABLE_SOURCE:
        return TOOLS
    return [tool for tool in TOOLS if tool["name"] not in WRITING_TOOLS]


def run_tool(name, args, occurrences, chunks, source=WRITABLE_SOURCE):
    """Run the tool the model asked for and return its output as text.

    Errors are returned rather than raised. That looks wrong until you notice
    where the return value goes: straight back to the model. A raised exception
    kills the conversation, whereas "the tool failed: I do not understand the
    date 'blorp', try 'today', 'next week'..." is something the model reads and
    corrects on its very next step.

    Errors a model can read are information. Errors that crash are not.
    """
    # Belt and braces. `tools_for` already keeps these out of the model's
    # reach; this catches a call that arrives some other way.
    if name in WRITING_TOOLS and source != WRITABLE_SOURCE:
        return (f"{name} needs --source {WRITABLE_SOURCE}. This session is "
                f"reading from {source!r}, and writing would change a "
                "different calendar from the one being shown.")
    try:
        if name == "find_events":
            return queries.find_events(occurrences, **args)
        if name == "upcoming_birthdays":
            return queries.upcoming_birthdays(occurrences, config.NOW, **args)
        if name == "directions":
            return queries.directions(occurrences, **args)
        if name == "find_free_time":
            return queries.find_free_time(occurrences, **args)
        if name == "search_notes":
            return search.search_notes(chunks, **args)
        if name == "remember_fact":
            memory.save_fact(args["fact"])
            return f"Saved: {args['fact']}"
        if name == "create_event":
            # Imported here rather than at the top: the Google libraries and a
            # sign-in are only needed by someone who actually writes.
            from assistant import google_api
            return google_api.create_event(occurrences, **args)
        if name == "delete_event":
            from assistant import google_api
            return google_api.delete_event(occurrences, **args)
        if name == "update_event":
            from assistant import google_api
            return google_api.update_event(occurrences, **args)
        # Models occasionally invent a tool name.
        return f"There is no tool called {name!r}."
    except Exception as error:
        return f"The tool {name} failed: {error}"


def build_system_prompt():
    """Standing instructions sent with every message.

    The current date is the part that is not optional. The model has no clock,
    so without it "what's on Tuesday?" gets answered about some Tuesday from
    training data. Long-term facts are appended so anything the user asked to
    be remembered survives a restart.
    """
    prompt = SYSTEM_PROMPT.format(
        today=f"{config.NOW:%A %d %B %Y}", time=f"{config.NOW:%H:%M}")
    facts = memory.facts_for_prompt()
    return f"{prompt}\n\n{facts}" if facts else prompt


def ask(question, occurrences, chunks, conversation=None, on_tool_call=None,
        source=WRITABLE_SOURCE):
    """Answer one question, running whatever tools the model asks for.

    `on_tool_call(name, args)` is invoked before each tool runs - watching the
    model pick tools is the fastest way to tell a good description from a bad
    one. Pass a conversation to keep context across questions.
    """
    conversation = conversation or memory.Conversation()
    conversation.add_user(question)
    system = build_system_prompt()

    for _ in range(MAX_STEPS):
        reply = call_model(system, conversation.recent(),
                           tools=tools_for(source))
        # The model's own reply must be stored before the tool results: the API
        # requires each tool_use block and its tool_result to sit next to each
        # other, in that order.
        conversation.add_assistant(reply["content"])

        if not reply.get("tool_calls"):
            return reply["text"]

        results = []
        for call in reply["tool_calls"]:
            if on_tool_call:
                on_tool_call(call["name"], call["input"])
            results.append((call["id"],
                            run_tool(call["name"], call["input"], occurrences,
                                  chunks, source)))
        conversation.add_tool_results(results)

    return (f"I used too many steps ({MAX_STEPS}) without reaching an answer. "
            "Try asking something more specific.")