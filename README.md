# Calendar Assistant

A conversational assistant for a calendar and a folder of meeting notes. It
answers questions like these:

```
you > whose birthday is coming up?

Ana Ortiz's birthday is this Friday, 7 August - four days away.
Grace Okafor's is the following Wednesday, the 12th.

you > when could I fit a 90 minute review this week?

Wednesday is completely free. Otherwise Monday after 11:30,
or Thursday 09:00-13:00.

you > what did we decide about the Northwind renewal?

You moved to the annual commit tier - Priya signed off. It saves
about 18%, and the twelve-month lock-in was judged acceptable.
(from 2026-07-30-vendor-contract-review.md)
```

Three questions, and **two of them never touch a language model or a vector
search.** That is the point of the project.

## The design decision

The obvious way to build this is to embed everything and do similarity search.
For a calendar that does not work, and the reason is worth stating plainly:

**The answer is not in the file.** A calendar does not contain "Ana's birthday
is 7 August 2026". It contains `DTSTART:19930807` and `RRULE:FREQ=YEARLY`. The
date has to be *computed*. No amount of semantic search produces a fact the
text does not contain.

Even where the text does contain the answer, "coming up" means *filter by date
range, sort ascending, take the first few* - a query, not a similarity ranking.
Ranking events by how much they resemble the word "birthday" returns things
that sound birthday-ish, in arbitrary order.

So the assistant uses two retrieval strategies and picks between them by
question type:

| Question | Handled by | Because |
|---|---|---|
| "what's on Tuesday?" | filtering (`queries`) | it's a filter |
| "whose birthday is coming up?" | filtering + date maths (`queries`) | it's a computation |
| "when am I free?" | interval arithmetic (`queries`) | it's arithmetic |
| "what did we decide about X?" | cosine similarity (`search`) | prose has no schema |
| "that meeting about the vendor thing" | cosine similarity (`search`) | the title is forgotten |

The model's job is not to know things. It picks which function to call and
turns the result into a sentence.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m assistant.main                    # chat
python -m assistant.main agenda "next week" # no model needed
python -m assistant.main birthdays          # no model needed
```

Without `ANTHROPIC_API_KEY` set it falls back to an offline keyword-matching
model, which exercises every code path except the phrasing of the final answer.

The bundled sample calendar is dated around Monday 3 August 2026, so "now"
defaults to that date. Point it at a real calendar and use the real clock:

```bash
export CALENDAR_FILE=~/exported.ics
export CALENDAR_NOW=now
```

## Connecting a real Google Calendar

Two routes, both ending at the same event dictionary.

**Secret iCal URL** — no OAuth, no cloud project. In Google Calendar: Settings
→ your calendar → Integrate calendar → *Secret address in iCal format*.

```bash
export GOOGLE_ICS_URL='https://calendar.google.com/calendar/ical/.../basic.ics'
export CALENDAR_NOW=now

python -m assistant.main agenda "this week" --source url
python -m assistant.main cache      # save a local copy, then use --source file
```

That URL is a credential, not a location: anyone holding it can read the whole
calendar forever without logging in. Keep it in the environment, never in the
source, and never in a screenshot.

**Calendar API** — real OAuth, and worth it if you want to extend beyond
reading. In the [Google Cloud console](https://console.cloud.google.com):

1. Create a project, then enable the **Google Calendar API**.
2. Configure the OAuth consent screen as **External**, and add your own
   address under *Test users*.
3. Credentials → Create credentials → **OAuth client ID** → *Desktop app* →
   download the JSON as `credentials.json` in the project root.

```bash
pip install google-api-python-client google-auth-oauthlib
python -m assistant.main agenda "this week" --source api
```

The first run opens a browser; `token.json` is written afterwards and reused.
While the consent screen stays in *Testing* status Google expires refresh
tokens after seven days, so expect to re-authorise weekly until you publish it.

### Two decisions inside the API route

**`singleEvents=False`.** Google will happily expand recurring events for you.
Letting it do so would make `recurrence.py` dead code, so the master event and
its `RRULE` string are fetched instead and expanded here. Google returns raw
iCalendar lines in its `recurrence` field, so the rule passes straight through
untouched.

**No `timeMin`/`timeMax` when not expanding.** Those bounds apply to the
*master* event, so a standup that began in 2019 — or a birthday whose start
date is in 1993 — would be filtered out before its upcoming instances were ever
generated. Everything is fetched, paginated, and windowed by `expand_all`.

### What the conversion is actually teaching

`recurrence`, `queries`, `search`, `memory` and `agent` did not change by one
line to support Google. The only new code is `convert_event`, which turns
Google's JSON into the dictionary `ics_parser.new_event()` defines. The event
dictionary is a contract, and any source honouring it plugs in.

### Known gap

Google's **Birthdays** calendar is generated from Contacts. It is not part of
any calendar export and has no iCal address, so the headline birthday question
finds nothing on a real account unless birthdays exist as ordinary events.
Reading them properly means the People API and a second OAuth scope.

## How it fits together

```
data/sample_calendar.ics
      |
      v
ics_parser   .ics text  ->  event dicts          one dict per RULE
      |
      v
recurrence   rules      ->  dated occurrences    17 events -> ~350 occurrences
      |
      v
queries      occurrences + a question -> text    filters, sorting, interval maths
      |
      +---- agent   the model picks a tool, reads the text, writes the answer
      |               ^
data/notes/*.md       |
      |               |
      v               |
search       notes -> chunks -> vectors -> ranked matches
```

| Module | Responsibility |
|---|---|
| [`ics_parser.py`](assistant/ics_parser.py) | Parse `.ics` into dictionaries. Line unfolding, escaped text, quoted parameters. |
| [`recurrence.py`](assistant/recurrence.py) | Expand `RRULE` into dated occurrences, minus `EXDATE`s. |
| [`queries.py`](assistant/queries.py) | Date-phrase parsing, overlap filtering, birthdays, free-time search. |
| [`agent.py`](assistant/agent.py) | Tool schemas, dispatch, the system prompt, the tool-calling loop. |
| [`memory.py`](assistant/memory.py) | Conversation history and facts that survive a restart. |
| [`search.py`](assistant/search.py) | Chunking, word-count vectors, cosine similarity over notes. |
| [`google_calendar.py`](assistant/google_calendar.py) | Google iCal URL and REST API loaders, plus the JSON converter. |
| [`llm.py`](assistant/llm.py) | API wrapper, with an offline fallback model. |

## Details worth knowing

**Half-open ranges.** Every window is `[start, end)`. Make both ends inclusive
and an event at exactly midnight belongs to two different weeks at once.

**Overlap, not containment.** A meeting from 13:00-14:30 must appear when you
ask about 14:00-14:15. The test is `ends after start AND starts before end`.

**Merged intervals for free time.** Real calendars are double-booked - the
sample Thursday has a 13:00-14:30 kickoff *and* a 14:00-14:30 one-to-one.
Subtracting those separately reports free time that is not free, so busy blocks
are merged first.

**The model never does date arithmetic.** It passes phrases like `"next week"`
through verbatim and `parse_when` resolves them. Models are unreliable at date
maths and the failure is silent.

**Tool errors are returned, not raised.** A raised exception ends the
conversation; a returned string like *"I do not understand the date 'blorp',
try 'today', 'next week'..."* gets read by the model, which then calls the tool
correctly on its next step.

**Injected clock.** Nothing calls `datetime.now()` inline. "Now" arrives as a
setting, which is what makes every date-dependent function testable.

## Tests

```bash
pytest -q        # 76 tests, no network access required
```

Covers parsing (line folding, escaped commas, quoted parameters), recurrence
expansion and exclusions, every supported date phrase, overlap filtering,
interval merging, the tool loop including its step limit, conversation trimming
that keeps `tool_use`/`tool_result` pairs adjacent, the similarity maths, and the
Google JSON conversion (against recorded payloads, so no credentials needed).

## Deliberate simplifications

- **Timezones are ignored.** Every datetime is naive and treated as wall-clock.
- **Word counts, not embeddings.** The same maths as a real vector search with
  no API dependency, and a vector you can read to see why a result ranked where
  it did. Swapping in an embeddings API is a change to one function.
- **Facts are a JSON list, occurrences are dicts.** No database for a dataset
  this size.

## Not handled

- `RECURRENCE-ID` overrides, where one instance of a repeating event was moved
  or cancelled individually.
- Google Calendar's Birthdays calendar, which is generated from Contacts and
  appears in no calendar export.

---

Originally built as a course project. The test suite is adapted from the course
scaffolding; the sample calendar and notes are fictional.