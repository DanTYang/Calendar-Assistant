"""Short-term conversation state and long-term saved facts.

The model has no memory. Every API call starts from nothing, and an assistant
that appears to remember the last thing you said does so only because the whole
conversation was sent again. Memory is something the program builds:

  SHORT-TERM  the messages in this conversation, so follow-up questions work
              ("what's on next week?" ... "which of those is with Priya?")

  LONG-TERM   facts written to a JSON file, so they outlive the process
              ("remember that Priya is my manager")
"""

import json
from pathlib import Path

import config

FACTS_HEADER = "Things the user told you in earlier conversations:"


class Conversation:
    """The message list, kept in the shape the API expects.

    A message is {"role": ..., "content": [blocks]}, with three kinds of block:

        {"type": "text", "text": "what's on today?"}
        {"type": "tool_use", "id": "toolu_01", "name": "find_events", "input": {...}}
        {"type": "tool_result", "tool_use_id": "toolu_01", "content": "Mon 03 Aug..."}
    """

    def __init__(self, max_turns=10):
        self.messages = []
        self.max_turns = max_turns

    def add_user(self, text):
        """Add something the person typed."""
        self.messages.append({"role": "user", "content": [{"type": "text", "text": text}]})

    def add_assistant(self, content):
        """Add the model's reply, storing the raw block list unchanged.

        Flattening it to a string would discard any tool_use blocks, which the
        matching tool results have to refer back to.
        """
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results):
        """Send tool output back, as one message built from (call_id, output) pairs.

        Tool results carry role "user", not "assistant" and not "tool". It looks
        like a mistake and is not: from the API's point of view the model spoke,
        and now the outside world is answering.
        """
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": output}
                        for call_id, output in results],
        })

    def starts_a_turn(self, message):
        """True if this message begins a new round of conversation.

        A turn begins when the *person* types. A message full of tool results
        also has role "user", but the program wrote it, not the person.
        """
        return (message["role"] == "user"
                and any(block["type"] == "text" for block in message["content"]))

    def recent(self):
        """The last `max_turns` turns, so long conversations stop growing.

        Trimming by slicing the flat message list would eventually cut between
        a tool_use block and its tool_result, and the API rejects the whole
        request when that happens. Grouping into turns first keeps every pair
        together.
        """
        turns, current = [], []
        for message in self.messages:
            if self.starts_a_turn(message) and current:
                turns.append(current)
                current = []
            current.append(message)
        if current:
            turns.append(current)

        return [message for turn in turns[-self.max_turns:] for message in turn]


def load_facts(path=None):
    """Read saved facts, returning [] for a missing, corrupt or unexpected file.

    Crashing on startup because a file was truncated is a poor trade against
    starting up with no memories.
    """
    path = Path(path or config.FACTS_FILE)
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    # Valid JSON of the wrong shape is still unusable.
    return facts if isinstance(facts, list) else []


def save_fact(fact, path=None):
    """Add one fact if it is non-empty and new, and return the full list."""
    facts = load_facts(path)
    fact = fact.strip()
    if fact and fact not in facts:
        facts.append(fact)

    path = Path(path or config.FACTS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return facts


def facts_for_prompt(path=None):
    """Format saved facts for the system prompt, or "" when there are none.

    An empty header is worse than no header - it invites the model to invent
    something to fill it.
    """
    facts = load_facts(path)
    if not facts:
        return ""
    return "\n".join([FACTS_HEADER] + [f"- {fact}" for fact in facts])