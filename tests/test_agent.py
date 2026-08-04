"""Part 4 — tools and the agent loop."""


def tool_named(name):
    from assistant.agent import TOOLS

    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    raise AssertionError(f"no tool called {name!r} in TOOLS")


def test_all_five_tools_are_described():
    from assistant.agent import TOOLS

    names = {tool["name"] for tool in TOOLS}
    assert names == {"find_events", "upcoming_birthdays", "find_free_time",
                     "search_notes", "remember_fact"}


def test_each_tool_has_the_shape_the_api_expects():
    from assistant.agent import TOOLS

    for tool in TOOLS:
        assert set(tool) >= {"name", "description", "input_schema"}
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]
        assert len(tool["description"]) > 20, f"{tool['name']} needs a real description"


def test_find_events_declares_when_as_required():
    schema = tool_named("find_events")["input_schema"]
    assert "when" in schema["properties"]
    assert schema["required"] == ["when"]


def test_run_tool_calls_the_right_function(occurrences, chunks):
    from assistant.agent import run_tool

    out = run_tool("upcoming_birthdays", {"within_days": 30}, occurrences, chunks)
    assert "Ana Ortiz" in out


def test_run_tool_passes_optional_arguments_through(occurrences, chunks):
    from assistant.agent import run_tool

    out = run_tool("find_events", {"when": "this week", "person": "priya"},
                   occurrences, chunks)
    assert "1:1 with Priya" in out
    assert "Dentist" not in out


def test_run_tool_returns_an_error_message_instead_of_crashing(occurrences, chunks):
    from assistant.agent import run_tool

    # A bad date must come back as text the model can read and react to.
    # If this raises instead, the whole conversation dies.
    out = run_tool("find_events", {"when": "blorp"}, occurrences, chunks)
    assert isinstance(out, str)
    assert "fail" in out.lower() or "understand" in out.lower()


def test_run_tool_handles_an_unknown_tool_name(occurrences, chunks):
    from assistant.agent import run_tool

    assert isinstance(run_tool("teleport", {}, occurrences, chunks), str)


def test_system_prompt_tells_the_model_what_day_it_is():
    from assistant.agent import build_system_prompt

    prompt = build_system_prompt()
    # Without this the model answers about a Tuesday from its training data.
    assert "Monday 03 August 2026" in prompt


def test_ask_runs_a_tool_and_returns_an_answer(occurrences, chunks):
    from assistant.agent import ask

    answer = ask("whose birthday is coming up?", occurrences, chunks)
    assert isinstance(answer, str) and answer.strip()
    assert "Ana Ortiz" in answer


def test_ask_stops_instead_of_looping_forever(occurrences, chunks, monkeypatch):
    from assistant import agent

    def never_finishes(system, messages, tools=None, max_tokens=1200):
        return {
            "text": "",
            "tool_calls": [{"id": "t1", "name": "find_events", "input": {"when": "today"}}],
            "content": [{"type": "tool_use", "id": "t1", "name": "find_events",
                         "input": {"when": "today"}}],
        }

    monkeypatch.setattr(agent, "call_model", never_finishes)
    answer = agent.ask("loop forever", occurrences, chunks)
    assert "too many steps" in answer.lower()
