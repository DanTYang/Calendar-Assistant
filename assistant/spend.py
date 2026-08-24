"""What each person's questions cost, and when to stop answering.

Every question is two calls to the model - one to choose a tool, one to turn
its output into an answer - and the bill for all of them arrives on one card.
That is fine while the only user is the person paying. It stops being fine the
moment a link is shared, because nothing else in this project has any opinion
about how many questions a person may ask.

The ledger is per person per day, and the cost is computed from the usage the
API reports rather than estimated from message lengths. Estimating would drift
the moment a prompt changed, and the drift would be silent.

Two limits, because they fail differently:

  - a daily limit per person stops one runaway conversation
  - a monthly limit across everyone stops ten people each doing that

The second is the one that protects the card. Per-person limits alone multiply
by however many people show up.

Stored as JSON files under `data/spend/`, one per person, the same shape as
saved facts. That is single-instance storage: two processes would each keep
their own count and the real total would be the sum. Fine for one App Runner
instance, wrong the moment there are two - see README.
"""

import hashlib
import json
from datetime import date

import config


# Dollars per million tokens: (input, output, cache read, cache write).
#
# Cache reads are a tenth of the input price and cache writes a quarter more,
# which is what makes caching the system prompt worth doing: it is sent on
# every call and changes on none of them.
PRICES = {
    "claude-sonnet-5": (3.00, 15.00, 0.30, 3.75),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
}

# What an unrecognised model is assumed to cost. Deliberately the most
# expensive one here: guessing low would let a model this file has not been
# taught about run past the limit unnoticed.
FALLBACK_PRICE = PRICES["claude-opus-5"]


class LimitReached(Exception):
    """This person has spent their allowance for the day, or everyone has."""


def price_of(usage, model=None):
    """Turn one call's reported usage into dollars.

    `usage` is the shape `llm.call_model` passes back: plain integers, already
    separated into the four kinds that are billed differently.
    """
    model = model or config.MODEL
    # Match on prefix so a dated model id (claude-sonnet-5-20260114) prices the
    # same as the family it belongs to.
    rate = next((p for name, p in PRICES.items() if model.startswith(name)),
                FALLBACK_PRICE)
    per_in, per_out, per_read, per_write = rate
    return (
        usage.get("input", 0) * per_in
        + usage.get("output", 0) * per_out
        + usage.get("cache_read", 0) * per_read
        + usage.get("cache_write", 0) * per_write
    ) / 1_000_000


def _ledger_file(user_id):
    """One file per person, named by hash rather than by id.

    The same reasoning as saved facts: a user id arrives in a header, and a
    header can contain `../`. Hashing means the name cannot escape the folder
    however hostile the input, and it keeps identifiers off the filesystem.
    """
    who = hashlib.sha256((user_id or "shared").encode("utf-8")).hexdigest()[:16]
    return config.SPEND_FOLDER / f"{who}.json"


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A missing file is someone who has not asked anything yet. A corrupt
        # one is treated the same way rather than refusing to answer: losing a
        # day's count is better than locking someone out over a bad byte.
        return {}


def spent_today(user_id, today=None):
    """Dollars this person has spent since midnight."""
    day = str(today or date.today())
    return _read(_ledger_file(user_id)).get("days", {}).get(day, 0.0)


def spent_this_month(today=None):
    """Dollars everyone has spent this month, across all users."""
    month = str(today or date.today())[:7]
    total = 0.0
    if not config.SPEND_FOLDER.exists():
        return total
    for path in config.SPEND_FOLDER.glob("*.json"):
        for day, amount in _read(path).get("days", {}).items():
            if day.startswith(month):
                total += amount
    return total


def check(user_id, today=None):
    """Refuse before the call rather than after it.

    Checked up front because the alternative - answering and then noticing -
    means the limit is always exceeded by one question, and one question is
    unbounded if the model loops.
    """
    if config.DAILY_LIMIT_USD:
        spent = spent_today(user_id, today)
        if spent >= config.DAILY_LIMIT_USD:
            raise LimitReached(
                f"You have used your allowance for today "
                f"(${spent:.2f} of ${config.DAILY_LIMIT_USD:.2f}). "
                f"It resets at midnight.")

    if config.MONTHLY_LIMIT_USD:
        total = spent_this_month(today)
        if total >= config.MONTHLY_LIMIT_USD:
            raise LimitReached(
                "This assistant has reached its shared monthly budget. "
                "Nothing is wrong with your question - it will work again "
                "next month, or when the owner raises the limit.")


def record(user_id, usage, model=None, today=None):
    """Add one call to the ledger and return what it cost.

    Written on every call rather than once per question, so a conversation
    that dies halfway through has still been counted. The tokens were spent
    whether or not an answer came back.
    """
    cost = price_of(usage, model)
    if not cost:
        return 0.0

    day = str(today or date.today())
    path = _ledger_file(user_id)
    ledger = _read(path)
    days = ledger.setdefault("days", {})
    days[day] = days.get(day, 0.0) + cost

    # Only the current month is kept. This is a spending limit, not an
    # accounting record, and old days are just a file that grows forever.
    month = day[:7]
    ledger["days"] = {d: v for d, v in days.items() if d[:7] == month}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger), encoding="utf-8")
    return cost


def summary(user_id, today=None):
    """What is left, for anyone who wants to show it."""
    spent = spent_today(user_id, today)
    return {
        "spent_today": round(spent, 4),
        "daily_limit": config.DAILY_LIMIT_USD or None,
        "remaining_today": (round(max(0.0, config.DAILY_LIMIT_USD - spent), 4)
                            if config.DAILY_LIMIT_USD else None),
        "spent_this_month": round(spent_this_month(today), 4),
        "monthly_limit": config.MONTHLY_LIMIT_USD or None,
    }
