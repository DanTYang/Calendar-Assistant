# Calendar Assistant

A calendar and meeting-notes assistant. `README.md` explains the architecture
and the design decision the project is built around.

## Run and test

```bash
.venv/bin/pytest -q                          # 66 tests, no network
.venv/bin/pytest tests/test_queries.py -q    # one module
.venv/bin/python -m assistant.main           # chat (offline model without an API key)
.venv/bin/python -m assistant.main agenda "next week"
```

## Conventions

- **Query functions return strings, not printed output.** Their return value is
  what gets sent to the model, so printing sends it nowhere useful.
- **Half-open ranges, `[start, end)`, everywhere.** Overlap is
  `end > start and start < end` - never containment.
- **The clock is injected.** Read `config.NOW`; never call `datetime.now()`
  inline. Tests pin `config.DEMO_NOW` so they cannot depend on the real date.
- **Tool functions return errors as text rather than raising.** The string goes
  back to the model, which reads it and retries. See `agent.run_tool`.
- **Naive datetimes throughout.** Timezones are a documented simplification.
- The event dictionary from `ics_parser.new_event()` is the contract every
  other module depends on. Any new calendar source converts to that shape
  rather than changing anything downstream.

## Things that look wrong and are not

- Tool results are sent back with `role: "user"`, not `"assistant"` or
  `"tool"`. That is what the API expects.
- `Conversation.recent()` trims whole turns rather than slicing the message
  list, because a `tool_use` block and its `tool_result` must stay adjacent.
- `recurrence.expand_event` nudges the lower bound back by the event duration
  so a meeting already in progress is generated before overlap is tested.

## Test data

`data/sample_calendar.ics` and `data/notes/` are fictional and dated around
3 August 2026, which is why `config.DEMO_NOW` is pinned there. Changing either
will break tests that assert specific events and dates.