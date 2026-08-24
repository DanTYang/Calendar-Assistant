# Calendar Assistant

A calendar and meeting-notes assistant, in two services: a Python process that
owns calendars and a Spring Boot gateway that owns identity. `README.md`
explains the architecture; `DECISIONS.md` lists the choices still worth
arguing about.

## Run

```bash
.venv/bin/python -m assistant.main            # chat in the terminal
.venv/bin/python -m assistant.main agenda "next week"   # no model needed
.venv/bin/python -m assistant.main login      # sign in to Google
.venv/bin/python -m assistant.web             # the HTTP service, port 5000
./gateway/run.sh                              # the gateway, port 8080
docker compose up --build                     # both, plus Postgres
```

Without `ANTHROPIC_API_KEY`, a keyword-matching stand-in answers instead of the
model. It understands nothing but exercises every other code path, which is why
the suite runs offline and free.

On macOS, port 5000 is also AirPlay Receiver, bound to `::1` while Flask binds
`127.0.0.1`. Use `127.0.0.1:5000`; `localhost:5000` can reach AirPlay and
return a puzzling 403.

## Conventions

- **Query functions return strings, not printed output.** Their return value is
  what gets sent to the model, so printing sends it nowhere useful.
- **Half-open ranges, `[start, end)`, everywhere.** Overlap is
  `end > start and start < end` - never containment.
- **The clock is injected.** Read `config.NOW`; never call `datetime.now()`
  inline.
- **Tool functions return errors as text rather than raising.** The string goes
  back to the model, which reads it and retries. See `agent.run_tool`.
- **Datetimes are naive, and converted at the edges.** Nothing in the middle of
  the program carries a timezone; `ics_parser` converts UTC timestamps to
  `config.TIMEZONE` as it reads them, and `google_api` does the same.
- The event dictionary from `ics_parser.new_event()` is the contract every
  other module depends on. Any new calendar source converts to that shape
  rather than changing anything downstream.
- **Anything that changes a calendar is two calls.** The first describes what
  would happen and changes nothing; the second, carrying `confirm`, does it.
  `agent.TWO_PHASE` and `agent.CONFIRM_FIELD` are shared so the rule is stated
  once rather than per tool.

## Things that look wrong and are not

- Tool results are sent back with `role: "user"`, not `"assistant"` or
  `"tool"`. That is what the API expects.
- `Conversation.recent()` trims whole turns rather than slicing the message
  list, because a `tool_use` block and its `tool_result` must stay adjacent.
- `recurrence.expand_event` nudges the lower bound back by the event duration
  so a meeting already in progress is generated before overlap is tested.
- `agent.ask` is a wrapper around `agent.ask_stream`. One loop, not two - the
  second copy would be the one that quietly stopped matching.
- The calendar service reads identity from a header and does not verify it. It
  cannot: it never sees a password. `GATEWAY_SECRET` is what stops anyone else
  from setting that header.

## The seam between the two services

Three headers, and nothing else:

| | |
|---|---|
| `X-User-Id` | this application's id for the person, not their Google subject |
| `X-Google-Token` | an access token the gateway has already refreshed |
| `X-Gateway-Key` | proof the caller is the gateway |

The Python service holds no session and reads no credential from disk, which is
what lets it answer for any caller. Keep it that way: state that belongs to a
person belongs in the gateway's database or in a header.

## Secrets

Settings come from the environment, loaded from a git-ignored `.env` by
`config.py`. `.env.example` is committed and must never contain real values.

`GOOGLE_ICS_URL` is a credential, not a location - it grants permanent read
access to an entire calendar with no login. Never print it in full, never put
it in a commit or a screenshot. Same for `credentials.json`, `token.json`,
`OAuthCrediential.json`, `gateway/data/` (which holds refresh tokens),
`data/facts/`, `data/spend/`, and any real exported `.ics`. `.gitignore` covers
all of them; if one is about to be committed, stop and say so.

## Sample data

`data/sample_calendar.ics` and `data/sample_notes/` are fictional and dated
around 3 August 2026, which is why `config.DEMO_NOW` is pinned there. Changing
either will break anything that asserts specific events and dates.
