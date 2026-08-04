"""Part 5 — memory."""


def test_conversation_records_what_was_said():
    from assistant.memory import Conversation

    chat = Conversation()
    chat.add_user("hi")
    chat.add_assistant([{"type": "text", "text": "hello"}])

    assert len(chat.messages) == 2
    assert chat.messages[0]["role"] == "user"
    assert chat.messages[0]["content"][0]["text"] == "hi"
    assert chat.messages[1]["role"] == "assistant"


def test_tool_results_go_back_as_a_user_message():
    from assistant.memory import Conversation

    chat = Conversation()
    chat.add_tool_results([("t1", "Ana Ortiz - Friday 07 August")])

    message = chat.messages[-1]
    # This looks wrong but it is what the API requires: tool output is delivered
    # in a message with role "user".
    assert message["role"] == "user"
    assert message["content"][0]["type"] == "tool_result"
    assert message["content"][0]["tool_use_id"] == "t1"


def test_a_tool_result_message_does_not_start_a_new_turn():
    from assistant.memory import Conversation

    chat = Conversation()
    chat.add_user("what's on today?")
    chat.add_tool_results([("t1", "nothing")])

    assert chat.starts_a_turn(chat.messages[0]) is True
    assert chat.starts_a_turn(chat.messages[1]) is False


def test_recent_keeps_tool_calls_with_their_results():
    from assistant.memory import Conversation

    chat = Conversation(max_turns=1)
    for question in ("first question", "second question"):
        chat.add_user(question)
        chat.add_assistant([{"type": "tool_use", "id": "t1", "name": "find_events",
                             "input": {"when": "today"}}])
        chat.add_tool_results([("t1", "some events")])
        chat.add_assistant([{"type": "text", "text": "here you go"}])

    kept = chat.recent()
    used = [b["id"] for m in kept for b in m["content"] if b.get("type") == "tool_use"]
    answered = [b["tool_use_id"] for m in kept for b in m["content"]
                if b.get("type") == "tool_result"]
    # Every tool call in the window must have its result. Cutting between them
    # makes the API reject the whole request.
    assert sorted(used) == sorted(answered)
    assert kept[0]["content"][0]["text"] == "second question"


def test_recent_keeps_everything_when_under_the_limit():
    from assistant.memory import Conversation

    chat = Conversation(max_turns=10)
    chat.add_user("only question")
    chat.add_assistant([{"type": "text", "text": "only answer"}])
    assert len(chat.recent()) == 2


def test_facts_are_saved_and_reloaded(tmp_path):
    from assistant.memory import load_facts, save_fact

    path = tmp_path / "facts.json"
    assert load_facts(path) == []

    save_fact("Priya is my manager", path)
    save_fact("I prefer mornings for deep work", path)
    assert load_facts(path) == ["Priya is my manager", "I prefer mornings for deep work"]


def test_the_same_fact_is_not_saved_twice(tmp_path):
    from assistant.memory import load_facts, save_fact

    path = tmp_path / "facts.json"
    save_fact("Priya is my manager", path)
    save_fact("Priya is my manager", path)
    assert len(load_facts(path)) == 1


def test_facts_for_prompt_is_empty_when_there_are_none(tmp_path):
    from assistant.memory import facts_for_prompt

    assert facts_for_prompt(tmp_path / "nothing.json") == ""


def test_facts_for_prompt_lists_what_was_saved(tmp_path):
    from assistant.memory import facts_for_prompt, save_fact

    path = tmp_path / "facts.json"
    save_fact("Priya is my manager", path)
    assert "Priya is my manager" in facts_for_prompt(path)
