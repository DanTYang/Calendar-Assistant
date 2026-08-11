"""
=============================================================================
GIVEN CODE — you do not need to edit this file.
=============================================================================

This is the thin layer that talks to the language model. It is given to you so
you can spend your time on the interesting parts instead of on API plumbing.

Two things worth knowing about it:

1. If you have not set an API key, it falls back to a FAKE model that picks
   tools using simple keyword matching. That is not intelligence — it is a
   stand-in so you can build and test Parts 1-6 without spending a cent. When
   you set ANTHROPIC_API_KEY, the exact same code runs against the real model.

2. `call_model()` always returns the same dictionary shape:

       {
         "text":       "the words the model said (may be empty)",
         "tool_calls": [ {"id": ..., "name": ..., "input": {...}}, ... ],
         "content":    [ raw blocks — append this straight into your history ]
       }
"""

import os
import re
import uuid

import config


def have_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def call_model(system, messages, tools=None, max_tokens=1200):
    """Send a conversation to the model and get one reply back.

    system:   a string of instructions ("You are a calendar assistant...")
    messages: the conversation so far, e.g.
                  [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    tools:    a list of tool descriptions (you build these in Part 4)
    """
    if not have_api_key():
        return _fake_model(system, messages, tools)

    import anthropic

    client = anthropic.Anthropic()
    kwargs = {
        "model": config.MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        # Effort is nested here, not top-level. Thinking itself is left alone:
        # it stays adaptive, because a model with thinking switched off reaches
        # for tools less readily, and every answer in this project comes from a
        # tool.
        "output_config": {"effort": config.MODEL_EFFORT},
    }
    if tools:
        kwargs["tools"] = tools

    response = client.messages.create(**kwargs)
    content = [block.model_dump() for block in response.content]
    return {
        "text": "\n".join(b.get("text", "") for b in content if b["type"] == "text").strip(),
        "tool_calls": [
            {"id": b["id"], "name": b["name"], "input": b.get("input") or {}}
            for b in content
            if b["type"] == "tool_use"
        ],
        "content": content,
    }


# ---------------------------------------------------------------------------
# The fake model. Keyword matching, nothing more. Good enough to prove your
# agent loop works; useless for anything requiring actual understanding.
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
