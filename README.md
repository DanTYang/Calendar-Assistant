# Calendar Assistant

A command-line assistant that answers natural-language questions about a Google
Calendar and a folder of markdown meeting notes, using the Claude API.

## Overview

The assistant loads a calendar, expands its recurrence rules into dated
occurrences, indexes a folder of notes, and exposes both to a language model as
callable tools. The model selects a tool, receives plain text back, and composes
the reply. All date arithmetic, filtering, and ranking is performed in Python;
the model performs none of it.

Calendars are supported from three sources, all of which produce the same event
dictionary:

| Source | Flag | Requirements |
|---|---|---|
| Google Calendar secret iCal URL | `--source url` | `GOOGLE_ICS_URL` |
| Google Calendar API (OAuth) | `--source api` | `credentials.json`, two extra packages |
| Local `.ics` file | `--source file` (default) | `CALENDAR_FILE` |

A calendar file stores recurrence rules, not individual meetings. A weekly
standup is one entry with `RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR`; the instance
falling on any given Monday exists nowhere in the file until it is computed.
Birthdays are stored as `DTSTART:19930807` with `FREQ=YEARLY`. Every rule is
therefore expanded into dated occurrences across a ±365 day window before any
query runs.

Note retrieval is a vector search: notes are split into paragraph-sized chunks,
each chunk is converted to a word-frequency vector, and chunks are ranked
against the query by cosine similarity.

Without `ANTHROPIC_API_KEY` the application runs against an offline
keyword-matching model that selects tools by regular expression and prints raw
tool output. Every code path except answer phrasing is exercised.

## Demo

<!-- Record a 10-20s terminal session and save it as docs/demo.gif -->

![Demo](docs/demo.gif)

## Architecture

```
Google Calendar (secret iCal URL)
        |
        v
ics_parser      .ics text  ->  event dicts        one dict per rule
        |
        v
recurrence      rules      ->  dated occurrences  expands RRULE, removes EXDATEs
        |
        v
queries         occurrences + question -> text    filtering, date math, intervals
        |
        +------ agent      selects a tool, reads the result, composes the answer
        |                        ^
data/notes/*.md                  |
        |                        |
        v                        |
search          notes -> chunks -> vectors -> ranked matches
```

The dictionary returned by `ics_parser.new_event()` is the interface between
the calendar sources and everything downstream. A new source converts to that
shape; no other module changes.

### Modules

| File | Responsibility |
|---|---|
| [`ics_parser.py`](assistant/ics_parser.py) | Parses `.ics` text into event dictionaries. Handles line folding, escaped characters, quoted parameters. |
| [`recurrence.py`](assistant/recurrence.py) | Expands `RRULE` into dated occurrences and removes `EXDATE` exclusions. |
| [`queries.py`](assistant/queries.py) | Date-phrase parsing, overlap filtering, birthday computation, free-time search. |
| [`search.py`](assistant/search.py) | Note chunking, word-frequency vectors, cosine similarity ranking. |
| [`agent.py`](assistant/agent.py) | Tool schemas, system prompt, tool-dispatch loop (max 5 steps). |
| [`memory.py`](assistant/memory.py) | Conversation history and facts persisted to disk. |
| [`google_calendar.py`](assistant/google_calendar.py) | iCal URL downloader, Calendar API client, JSON-to-event conversion. |
| [`llm.py`](assistant/llm.py) | Claude API wrapper with an offline fallback model. |
| [`config.py`](config.py) | Settings resolved from the environment, including the injected clock. |

### Tools exposed to the model

| Tool | Parameters | Returns |
|---|---|---|
| `find_events` | `when`, optional `person` | Events in a date range, optionally filtered by attendee |
| `find_free_time` | `when`, `duration_minutes` | Gaps in working hours long enough for the requested duration |
| `upcoming_birthdays` | `within_days` | Birthdays in the window, sorted by proximity |
| `search_notes` | `query` | Highest-ranked note chunks with their source filenames |
| `remember_fact` | `fact` | Confirmation; the fact is appended to every later system prompt |

Tool errors are returned to the model as text rather than raised. An
unrecognised date phrase produces the list of supported phrases, which the model
reads before retrying.

### Supported date phrases

`parse_when` accepts only the following. Anything else raises and returns the
list to the model.

| Phrase | Range |
|---|---|
| `today`, `tomorrow`, `yesterday` | One day, midnight to midnight |
| `this week`, `next week`, `last week` | Calendar week, Monday to Sunday |
| `this month`, `next month`, `last month` | Calendar month |
| `next N days` | Today through N days ahead |
| `last N days` | N days back through end of today |
| `YYYY-MM-DD` | That single day |
| `monday` … `sunday` | The next such weekday, never today |
| `next monday` … `next sunday` | The weekday in the following week |

All ranges are half-open, `[start, end)`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Developed against Python 3.12. The `--source api` route additionally requires
`google-api-python-client` and `google-auth-oauthlib`.

### Configuration

Settings are read from the environment. `config.py` loads a git-ignored `.env`
file; real environment variables take precedence over it.

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the real model. Without it, the offline fallback is used. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Model identifier |
| `GOOGLE_ICS_URL` | unset | Secret iCal address, required for `--source url` |
| `CALENDAR_FILE` | `data/sample_calendar.ics` | Local calendar for `--source file` |
| `CALENDAR_NOW` | pinned to 2026-08-03 09:00 | Set to `now` to use the system clock |
| `NOTES_FOLDER` | `data/notes` | Directory of `.md` files to index |
| `WORK_START_HOUR` | `9` | Lower bound for free-time search |
| `WORK_END_HOUR` | `17` | Upper bound for free-time search |
| `GOOGLE_CALENDAR_ID` | `primary` | Calendar read by `--source api` |

`CALENDAR_NOW` defaults to a fixed date because the bundled sample calendar is
built around 3 August 2026. Set it to `now` when using a real calendar.

The secret iCal address is obtained from Google Calendar under Settings → the
calendar → Integrate calendar → **Secret address in iCal format**.

> **The iCal URL is a credential.** It grants permanent read access to the
> entire calendar without authentication and does not expire until reset. It
> belongs in `.env`, which is git-ignored, along with `credentials.json` and
> `token.json`.

### macOS certificate configuration

Python distributions from python.org install no root certificates, causing
`CERTIFICATE_VERIFY_FAILED` on the calendar download. Install them once:

```bash
"/Applications/Python 3.12/Install Certificates.command"
```

## Usage

```bash
python -m assistant.main [command] [--source file|url|api]
```

| Command | Behaviour | Model required |
|---|---|---|
| *(none)* | Interactive chat | Yes |
| `agenda "<phrase>"` | Prints events in a date range | No |
| `birthdays` | Prints upcoming birthdays | No |
| `cache` | Downloads `GOOGLE_ICS_URL` to `data/google_cache.ics` | No |

Verify the calendar loads before starting a chat session:

```bash
python -m assistant.main agenda "this week" --source url
```

Start the assistant:

```bash
python -m assistant.main --source url
```

```
you > what's on my calendar tomorrow?
you > when am I free for 90 minutes on Thursday?
you > what did we decide about the Northwind renewal?
you > quit
```

Each turn prints the selected tool and its arguments before the answer.

To avoid re-downloading the calendar on every start, cache it and read the
cached file:

```bash
python -m assistant.main cache
CALENDAR_FILE=data/google_cache.ics python -m assistant.main
```

### Google Calendar API route

Requires a Google Cloud project with the Calendar API enabled and an OAuth
client of type *Desktop app*, downloaded to `credentials.json` in the project
root. The first run opens a browser and writes `token.json`, which is reused
afterwards. While the consent screen remains in *Testing* status, Google expires
refresh tokens after seven days.

### Tests

```bash
pytest -q
```

76 tests, no network access required. Coverage includes parsing, recurrence
expansion, every supported date phrase, overlap filtering, interval merging, the
tool loop and its step limit, conversation trimming, similarity computation, and
Google JSON conversion against recorded payloads.

## Design decisions

**Recurrence is expanded locally, not by the Calendar API.**
`fetch_api_events` requests master events with `singleEvents=False` and expands
them in `recurrence.py`. Rationale: a single expansion path serves all three
calendar sources. Trade-off: edge cases such as `RECURRENCE-ID` overrides become
this project's responsibility, and one of them is unhandled.

**The full calendar is fetched, not a date-bounded slice.**
`timeMin` and `timeMax` apply to the master event rather than its instances, so
a series beginning in 2019 or a birthday dated 1993 would be excluded before the
current instance could be generated. Rationale: correctness of the window.
Trade-off: more data transferred and slower startup on large calendars.

**The model performs no date arithmetic.**
Date phrases are passed through verbatim and resolved by `parse_when`. Rationale:
language models produce incorrect dates with high confidence, making the failure
silent. Trade-off: only enumerated phrases are supported; anything else returns
an error string.

**Note vectors are word frequencies, not embeddings.**
`text_to_vector` counts tokens after stop-word removal. Rationale: the
similarity computation remains inspectable, and no additional API dependency is
introduced. Trade-off: matching is lexical, so a query for "pricing" does not
retrieve a note that consistently says "cost".

**Datetimes are naive throughout.**
No timezone conversion is performed at any layer; all times are treated as
wall-clock. Rationale: keeps interval and overlap logic readable. Trade-off:
calendars spanning timezones display incorrect times, and `RRULE` `UNTIL` values
written in UTC are interpreted as local time.

**Ranges are half-open and matched by overlap.**
Every window is `[start, end)`, and an occurrence qualifies if it overlaps the
window rather than being contained by it. Rationale: inclusive bounds place a
midnight event in two weeks simultaneously, and containment excludes a
13:00–14:30 meeting from a 14:00–14:15 query.

**Tool errors are returned as text, not raised.**
`run_tool` catches exceptions and returns the message. Rationale: a raised
exception terminates the conversation, whereas a returned string is read by the
model, which corrects its call on the next step. Trade-off: genuine defects
surface as messages rather than stack traces.

**The clock is injected.**
`config.NOW` is resolved once at import; `datetime.now()` is never called
inline. Rationale: date-dependent functions become testable against a fixed
date. Trade-off: with `CALENDAR_NOW=now` set, one test asserting the system
prompt contains 3 August 2026 fails.

## Limitations

**Read-only.** Events cannot be created, modified, or deleted. Write access
requires the OAuth route and an additional scope.

**Google's Birthdays calendar is unavailable.** It is generated from Contacts
rather than stored in a calendar, and appears in neither the iCal export nor the
API response. Birthdays are retrieved only where they exist as ordinary
recurring events. Full support requires the People API and a second OAuth scope.

**`RECURRENCE-ID` overrides are ignored.** An individually rescheduled or
cancelled instance of a recurring event is recorded as a separate component,
which the parser skips. The instance appears at its original time.

**Lexical note matching.** See the word-frequency decision above. Replacing
`text_to_vector` with an embeddings call is the intended next change.

**No timezone support.** See the naive-datetime decision above.

**Offline fallback is not a model.** Without `ANTHROPIC_API_KEY`, tool selection
is regular-expression matching and the response is raw tool output.

---

The sample calendar in `data/` and the notes in `data/notes/` are fictional. The
test suite originated as course scaffolding.
