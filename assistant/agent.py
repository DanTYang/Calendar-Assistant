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

# Each entry describes one function in words the model can act on. `description`
# is not a comment for humans - it is the only thing the model uses to decide
# whether this is the right tool, so vagueness here shows up as wrong tool
# choices at runtime.
TOOLS = [
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

Keep answers short and lead with the dates and times."""


def run_tool(name, args, occurrences, chunks):
    """Run the tool the model asked for and return its output as text.

    Errors are returned rather than raised. That looks wrong until you notice
    where the return value goes: straight back to the model. A raised exception
    kills the conversation, whereas "the tool failed: I do not understand the
    date 'blorp', try 'today', 'next week'..." is something the model reads and
    corrects on its very next step.

    Errors a model can read are information. Errors that crash are not.
    """
    try:
        if name == "find_events":
            return queries.find_events(occurrences, **args)
        if name == "upcoming_birthdays":
            return queries.upcoming_birthdays(occurrences, config.NOW, **args)
        if name == "find_free_time":
            return queries.find_free_time(occurrences, **args)
        if name == "search_notes":
            return search.search_notes(chunks, **args)
        if name == "remember_fact":
            memory.save_fact(args["fact"])
            return f"Saved: {args['fact']}"
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


def ask(question, occurrences, chunks, conversation=None, on_tool_call=None):
    """Answer one question, running whatever tools the model asks for.

    `on_tool_call(name, args)` is invoked before each tool runs - watching the
    model pick tools is the fastest way to tell a good description from a bad
    one. Pass a conversation to keep context across questions.
    """
    conversation = conversation or memory.Conversation()
    conversation.add_user(question)
    system = build_system_prompt()

    for _ in range(MAX_STEPS):
        reply = call_model(system, conversation.recent(), tools=TOOLS)
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
                            run_tool(call["name"], call["input"], occurrences, chunks)))
        conversation.add_tool_results(results)

    return (f"I used too many steps ({MAX_STEPS}) without reaching an answer. "
            "Try asking something more specific.")