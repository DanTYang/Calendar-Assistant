"""The layer that talks to the language model.

Everything above this file works in one shape and one shape only:

    {
      "text":       the words the model said, possibly empty
      "tool_calls": [{"id": ..., "name": ..., "input": {...}}, ...]
      "content":    the raw blocks, appended straight into the conversation
      "usage":      {"input": n, "output": n, "cache_read": n, "cache_write": n}
    }

Keeping that shape fixed is what lets `agent.py` stay a loop over tool calls
rather than a translation layer, and it is why `usage` is reported here rather
than measured somewhere else - it is what the API said, not an estimate.
`spend.py` turns those numbers into dollars.

Without `ANTHROPIC_API_KEY` set, a keyword-matching stand-in answers instead.
It picks tools by looking for words and understands nothing, which is enough
to exercise the agent loop, the tools, and every test in the suite offline and
for free. The code path around it is identical: same shape in, same shape out.
"""

import os
import re
import uuid

import config


def have_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def call_model(system, messages, tools=None, max_tokens=1200):
    """Send a conversation to the model and get one reply back.

    system:   the instructions, as one string
    messages: the conversation so far, in the API's shape -
                  [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    tools:    the tool schemas the model may choose from, or None

    A thin wrapper over `stream_model`, for callers with nothing to do with
    the text until it is complete.
    """
    for kind, payload in stream_model(system, messages, tools, max_tokens):
        if kind == "reply":
            return payload


def stream_model(system, messages, tools=None, max_tokens=1200):
    """The same call, as a generator, so an answer can be shown as it arrives.

    Yields ("text", chunk) each time the model writes a little more, then
    exactly one ("reply", {...}) carrying the same dictionary `call_model`
    returns. Callers that do not care about the chunks ignore them - which is
    what `call_model` above does, so there is one code path rather than two.

    A question is several calls, and text can come from any of them: the model
    often says what it is about to do before reaching for a tool. All of it is
    real output and all of it is streamed.
    """
    if not have_api_key():
        # Costs nothing, but reports the same shape so no caller needs a branch
        # for it. Arrives in one piece: there is nothing to stream from a
        # keyword match, and pretending otherwise would only be theatre.
        reply = _fake_model(system, messages, tools)
        reply.setdefault("usage",
                         {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        if reply.get("text"):
            yield ("text", reply["text"])
        yield ("reply", reply)
        return

    import anthropic

    client = anthropic.Anthropic()
    kwargs = {
        "model": config.MODEL,
        "max_tokens": max_tokens,
        # Marked for caching: the instructions are several thousand tokens,
        # they are sent on every call, and they change on none of them. A cache
        # read is a tenth of the input price, so this is most of the bill for
        # a question that has already been asked once.
        "system": [{"type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        # Effort is nested here, not top-level. Thinking itself is left alone:
        # it stays adaptive, because a model with thinking switched off reaches
        # for tools less readily, and every answer in this project comes from a
        # tool.
        "output_config": {"effort": config.MODEL_EFFORT},
    }
    if tools:
        # The marker goes on the last tool only. It caches everything up to
        # that point, so one marker covers the whole block - and the tools are
        # as fixed as the system prompt is.
        tools = [dict(tool) for tool in tools]
        tools[-1]["cache_control"] = {"type": "ephemeral"}
        kwargs["tools"] = tools

    with client.messages.stream(**kwargs) as stream:
        for chunk in stream.text_stream:
            yield ("text", chunk)
        # Only complete once the stream is drained. This carries the tool calls
        # and the token counts, neither of which exists until the end.
        response = stream.get_final_message()

    content = [block.model_dump() for block in response.content]
    yield ("reply", {
        "text": "\n".join(b.get("text", "") for b in content if b["type"] == "text").strip(),
        "tool_calls": [
            {"id": b["id"], "name": b["name"], "input": b.get("input") or {}}
            for b in content
            if b["type"] == "tool_use"
        ],
        "content": content,
        "usage": _usage(response.usage),
    })


def _usage(reported):
    """The four numbers that are billed differently, as plain integers.

    Read with getattr because the cache fields are absent on responses from
    models or accounts where caching did not apply, and a missing field should
    read as zero rather than raise.
    """
    return {
        "input": getattr(reported, "input_tokens", 0) or 0,
        "output": getattr(reported, "output_tokens", 0) or 0,
        "cache_read": getattr(reported, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(reported, "cache_creation_input_tokens", 0) or 0,
    }


# ---------------------------------------------------------------------------
# The offline stand-in. Keyword matching, nothing more: enough to exercise the
# agent loop and the tools without an API key, and useless for anything that
# requires actually understanding the question.
# ---------------------------------------------------------------------------

def _fake_model(system, messages, tools):
    available = {t["name"] for t in (tools or [])}
    last = messages[-1] if messages else {"role": "user", "content": []}
    blocks = last.get("content", [])

    results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    if results:
        body = "\n\n".join(str(b.get("content", "")).strip() for b in results)
        text = "(fake model — showing raw tool output)\n\n" + body
        return {"text": text, "tool_calls": [], "content": [{"type": "text", "text": text}]}

    question = " ".join(
        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    )
    choice = _fake_route(question.lower(), available)
    if choice is None:
        text = (
            "(fake model — no ANTHROPIC_API_KEY set, so I am guessing with keywords)\n"
            f"You asked: {question!r}"
        )
        return {"text": text, "tool_calls": [], "content": [{"type": "text", "text": text}]}

    name, args = choice
    call = {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:10], "name": name,
            "input": args}
    return {
        "text": "",
        "tool_calls": [{"id": call["id"], "name": name, "input": args}],
        "content": [call],
    }


def _fake_route(q, available):
    when = _fake_when(q)
    if "remember" in q and "remember_fact" in available:
        return "remember_fact", {"fact": q}
    if re.search(r"birthday|anniversar", q) and "upcoming_birthdays" in available:
        days = 45
        match = re.search(r"(\d+)\s*(day|week|month)", q)
        if match:
            days = int(match.group(1)) * {"day": 1, "week": 7, "month": 30}[match.group(2)]
        return "upcoming_birthdays", {"within_days": days}
    if re.search(r"\bfree\b|available|fit\b|open slot", q) and "find_free_time" in available:
        minutes = 30
        match = re.search(r"(\d+)\s*(?:min|minute)", q)
        if match:
            minutes = int(match.group(1))
        elif "hour" in q:
            minutes = 60
        return "find_free_time", {"when": when, "duration_minutes": minutes}
    if re.search(r"decide|decided|discuss|agree|notes?|said|about the", q) \
            and "search_notes" in available:
        return "search_notes", {"query": q}
    if "find_events" in available:
        args = {"when": when}
        for person in ("priya", "marcus", "ana", "tomas", "grace"):
            if person in q:
                args["person"] = person
        return "find_events", args
    return None


def _fake_when(q):
    for phrase in ("today", "tomorrow", "yesterday", "this week", "next week", "last week",
                   "this month", "next month", "last month"):
        if phrase in q:
            return phrase
    match = re.search(r"next (\d+) days?", q)
    if match:
        return f"next {match.group(1)} days"
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        if day in q:
            return day
    return "next 7 days"
