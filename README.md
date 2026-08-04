# Calendar Assistant

A command-line assistant that answers questions about my real Google Calendar
and my meeting notes, in plain English, using Claude.

I built this to learn how the pieces actually fit together, so a fair amount of
it does by hand what a library would otherwise hide. The decisions section near
the bottom covers what I chose and what each choice cost me.

## What it is

I wanted to be able to ask my calendar things the way I'd ask a person. Not
"open the app and scroll to Thursday," but "when am I free for 90 minutes this
week?" or "what did we decide about the vendor renewal?" This is that.

It connects to a real Google Calendar. You give it the secret iCal address from
your calendar settings and it downloads the live `.ics` export every time it
starts, so what it tells you is what's actually on your calendar right now.
There's also an OAuth route through the Google Calendar API if you'd rather not
use a URL, and a plain-file mode for a calendar you've exported by hand.

The part that took the most work is that **a calendar file doesn't contain your
meetings.** It contains the rules that generate them. A weekly standup is one
entry that says "starts 25 May 2026, repeat weekly on Mon/Wed/Fri," and next
Monday's instance exists nowhere in the file until something calculates it.
Same with birthdays: the file says "7 August 1993, yearly," not "Ana's birthday
is this Friday." So before anything can answer a question, every rule has to be
expanded into actual dated occurrences across the window you care about. On my
calendar that turns ~300 stored events into thousands of dated ones.

Claude's job here is not to know my schedule. It gets five tools it can call —
find events, find free time, upcoming birthdays, search notes, remember a fact
— and it picks which one fits the question, reads the text that comes back, and
writes the reply. All the date arithmetic happens in Python, because models are
unreliable at date math and when they get it wrong they get it wrong
confidently. The model never computes "next Tuesday"; it passes the phrase
`"next week"` through and `parse_when` resolves it.

The notes half is separate. It reads a folder of markdown files, splits them
into paragraph-sized chunks, turns each chunk into a word-frequency vector, and
ranks them against the question by cosine similarity. That's a real vector
search, just with word counts standing in for embeddings for now (see
Limitations).

## Demo

<!-- Record a 10-20s terminal session and drop it in as docs/demo.gif -->

![Demo](docs/demo.gif)

## How it works

```
Google Calendar (secret iCal URL)
        |
        v
ics_parser      .ics text  ->  event dicts        one dict per RULE
        |
        v
recurrence      rules      ->  dated occurrences  expands RRULE, drops EXDATEs
        |
        v
queries         occurrences + question -> text    filtering, date math, intervals
        |
        +------ agent      model picks a tool, reads the result, writes the answer
        |                        ^
data/notes/*.md                  |
        |                        |
        v                        |
search          notes -> chunks -> vectors -> ranked matches
```

Everything downstream of `ics_parser` works on the same plain dictionary, so
the three calendar sources (iCal URL, Google API, local file) all converge to
one shape and nothing else has to know which one was used.

### The modules

| File | What it does |
|---|---|
| [`ics_parser.py`](assistant/ics_parser.py) | Turns `.ics` text into event dictionaries. Handles line folding, escaped characters, quoted parameters. |
| [`recurrence.py`](assistant/recurrence.py) | Expands `RRULE` into dated occurrences and removes `EXDATE` exclusions. |
| [`queries.py`](assistant/queries.py) | Parses date phrases, filters by overlap, finds birthdays, computes free time. |
| [`search.py`](assistant/search.py) | Chunks the notes, builds word-count vectors, ranks by cosine similarity. |
| [`agent.py`](assistant/agent.py) | Tool definitions, the system prompt, and the loop that runs tool calls. |
| [`memory.py`](assistant/memory.py) | Conversation history, and facts saved to disk so they survive a restart. |
| [`google_calendar.py`](assistant/google_calendar.py) | Downloads the iCal URL, or reads the Calendar API and converts its JSON. |
| [`llm.py`](assistant/llm.py) | Wraps the Claude API. Falls back to an offline keyword matcher with no API key. |
| [`config.py`](config.py) | All settings, read from `.env`. Includes the clock, so dates are testable. |

### The five tools

| Tool | Answers |
|---|---|
| `find_events` | "what's on Thursday?", "anything with Priya next week?" |
| `find_free_time` | "when could I fit 90 minutes this week?" |
| `upcoming_birthdays` | "whose birthday is coming up?" |
| `search_notes` | "what did we decide about the renewal?" |
| `remember_fact` | "remember that I prefer mornings for meetings" |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the config template and fill it in:

```bash
cp .env.example .env
```

Two values matter:

- `ANTHROPIC_API_KEY` — from https://console.anthropic.com. Without it the app
  still runs, but on an offline keyword matcher instead of a real model.
- `GOOGLE_ICS_URL` — in Google Calendar: Settings → your calendar → Integrate
  calendar → **Secret address in iCal format**.

Also set `CALENDAR_NOW=now`. Without it the clock is pinned to 3 August 2026,
which is the date the bundled sample calendar is built around.

**Treat the iCal URL like a password.** Anyone who has it can read the whole
calendar forever, with no login, and it doesn't expire until you reset it. It
lives in `.env`, which is git-ignored. Don't paste it anywhere else.

### macOS certificate error

If the download fails with `CERTIFICATE_VERIFY_FAILED`, your Python has no root
certificates installed. This happens with the python.org installer. Run this
once:

```bash
"/Applications/Python 3.12/Install Certificates.command"
```

## Running it

Check the calendar downloads and parses. This costs nothing — no model is
involved:

```bash
python -m assistant.main agenda "this week" --source url
```

Then start the chat:

```bash
python -m assistant.main --source url
```

```
you > what's on my calendar tomorrow?
you > when am I free for 30 minutes this week?
you > what did we decide about the Northwind renewal?
you > quit
```

Each turn prints which tool the model chose and what arguments it passed, which
is the fastest way to spot a tool description that's steering it wrong.

If re-downloading on every start gets slow, save a local copy and read that
instead:

```bash
python -m assistant.main cache
CALENDAR_FILE=data/google_cache.ics python -m assistant.main
```

### Other ways to run it

```bash
python -m assistant.main agenda "next week"    # print a date range, no model
python -m assistant.main birthdays             # print upcoming birthdays
python -m assistant.main --source api          # OAuth instead of the URL
python -m assistant.main                       # bundled sample calendar
```

The OAuth route needs a Google Cloud project with the Calendar API enabled and
a Desktop-app OAuth client downloaded to `credentials.json`. The first run opens
a browser and writes `token.json`. While the consent screen is in Testing status
Google expires the refresh token weekly.

### Tests

```bash
pytest -q
```

76 tests, no network needed. They cover parsing, recurrence expansion, every
date phrase, overlap filtering, interval merging, the tool loop, conversation
trimming, the similarity math, and the Google JSON conversion against recorded
payloads.

## Decisions I made, and what they cost

Most of these were a choice between something that would work sooner and
something I'd understand better. I usually picked the second one, and each has
a downside I decided to live with.

**Expanding recurrence myself instead of letting Google do it.** The Calendar
API will return pre-expanded instances of a repeating event if you ask for
them. I ask for the master events instead (`singleEvents=False`) and expand
them in `recurrence.py`. I wanted the expansion to be something I'd written,
and it means all three calendar sources go through one code path instead of
Google getting a special case. The cost is that I own the edge cases, including
the one below that I haven't handled.

**Fetching the whole calendar instead of just the window I need.** The API
takes `timeMin`/`timeMax`, but those bounds apply to the *master* event, not
its instances. A standup that began in 2019, or a birthday dated 1993, gets
filtered out before this week's occurrence is ever generated. So everything is
fetched and paginated, and the windowing happens after expansion. That's more
data over the wire than strictly necessary and it makes startup slower on a
large calendar.

**The model never does date arithmetic.** Claude passes phrases like
`"next week"` through untouched and `parse_when` resolves them in Python.
Models get date math wrong, and they're confident when they do, so the failure
is silent and easy to miss. The cost is that only the phrases I explicitly
handle work — ask for "the week after next" and you get an error message rather
than an answer.

**Word-frequency vectors instead of an embeddings API.** The search does real
cosine similarity, but the vectors are word counts, which means I can print one
and see exactly why a chunk ranked where it did. Calling an embeddings API
would have worked immediately and taught me nothing about what's underneath.
The cost is real: it only matches words a note literally contains, so "what did
we decide about pricing?" won't find a note that says "cost" throughout.
Swapping it out is a change to one function, `text_to_vector`, and it's the
next thing I want to do.

**Naive datetimes everywhere.** No timezone handling at all — every datetime is
wall-clock. I did this to keep the date math readable while I was still working
out the overlap and interval logic. It's the thing I'd fix first if this had to
be dependable: a calendar spanning timezones shows wrong times, and `RRULE` end
dates written in UTC get read as local time, so a repeating series can end a
few hours off.

**Half-open ranges and overlap instead of containment.** Every window is
`[start, end)`, and an event counts if it *overlaps* the window rather than
sitting inside it. Both rules are one line each and both prevent a whole
category of bug: make the ends inclusive and a midnight event lands in two
weeks at once; test for containment and a 13:00–14:30 meeting disappears when
you ask about 14:00–14:15.

**Tool errors are returned as text, not raised.** When the model calls a tool
with a date it doesn't understand, the tool hands back "I do not understand the
date 'blorp', try 'today', 'next week'..." instead of throwing. A raised
exception ends the conversation; a returned string gets read by the model,
which then calls the tool correctly on its next step. The downside is that a
real bug can surface as a polite message rather than a stack trace.

**The clock is a setting, not `datetime.now()`.** Nothing reads the system
clock inline; "now" comes from `config.NOW`. That's the only reason the
date-dependent functions are testable, since the tests pin it to a fixed day.
The side effect is that once you set `CALENDAR_NOW=now` in `.env`, one test
fails — the one asserting the system prompt contains 3 August 2026. That's the
test being date-dependent, not the app being broken.

## Still missing

- **It's read-only.** It answers questions but can't create, move, or delete
  anything. That needs the OAuth route plus a write scope.
- **Google's Birthdays calendar doesn't come through.** It's generated from
  Contacts rather than stored in a calendar, so it's in neither the iCal export
  nor the API response. Birthdays only appear if they exist as ordinary events.
  Reading them properly means the People API and another OAuth scope.
- **Individually-modified recurring events are ignored.** Move or cancel one
  instance of a repeating meeting and the calendar records a `RECURRENCE-ID`
  override, which the parser skips. That instance still shows at its original
  time.
- **Without an API key there are no real answers.** The offline fallback matches
  keywords to pick a tool and prints the raw output. Enough to confirm the
  plumbing works, not enough to hold a conversation.

---

The sample calendar in `data/` and the notes in `data/notes/` are fictional.
The test suite started from course scaffolding.
