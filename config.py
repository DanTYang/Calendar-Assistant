"""Settings, all overridable from the environment.

Secrets - the API key, the calendar URL - belong in a `.env` file next to this
one, which is git-ignored. `.env.example` shows the shape without the values.
Real environment variables win over anything in `.env`, so CI and containers
work without one.
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # optional - the app runs fine on real env vars alone
    pass

CALENDAR_FILE = Path(os.environ.get(
    "CALENDAR_FILE", PROJECT_ROOT / "data" / "sample_calendar.ics"))
NOTES_FOLDER = Path(os.environ.get(
    "NOTES_FOLDER", PROJECT_ROOT / "data" / "sample_notes"))
FACTS_FILE = Path(os.environ.get(
    "FACTS_FILE", PROJECT_ROOT / "data" / "facts.json"))

# The bundled sample calendar is built around Monday 3 August 2026, so "now"
# defaults to that date rather than the real clock - otherwise the demo returns
# an empty week. Point CALENDAR_FILE at your own calendar and set CALENDAR_NOW=now.
#
# Reading the clock through a setting instead of calling datetime.now() inline
# is also what makes every date-dependent function testable.
DEMO_NOW = datetime(2026, 8, 3, 9, 0)


def _resolve_now():
    setting = os.environ.get("CALENDAR_NOW", "")
    if setting.lower() == "now":
        return datetime.now()
    if setting:
        return datetime.fromisoformat(setting)
    return DEMO_NOW


NOW = _resolve_now()

# The one timezone this assistant assumes you are in.
#
# Everything inside the project stays naive - the interval and overlap logic is
# far easier to read that way, and it is correct as long as one zone is assumed.
# This setting exists for the edges where Google will not accept a naive time:
# creating an event, and asking for travel times.
#
# A zone name, not an offset. "EST" is -05:00 year round, so it is wrong from
# March to November; "America/New_York" tracks daylight saving on its own.
TIMEZONE = ZoneInfo(os.environ.get("CALENDAR_TIMEZONE", "America/New_York"))

# Where directions start from when nothing else is known. The calendar knows
# where an event is, never where you are, so without this every link begins at
# "wherever the phone is" - fine on a phone, useless from a laptop.
HOME_ADDRESS = os.environ.get("HOME_ADDRESS", "")

# What one person may spend in a day, and what everyone may spend in a month.
#
# A question costs about two cents: two calls to the model, roughly 5,400
# input tokens and 200 output. So $0.50 is about 25 questions, comfortably
# more than a day's real use and far short of what a loop could burn.
#
# The monthly ceiling is the one that protects the card. Ten people each
# reaching their daily limit is $150, and a per-person limit cannot see that
# coming. Set either to 0 to disable it.
DAILY_LIMIT_USD = float(os.environ.get("DAILY_LIMIT_USD", "0.50"))
MONTHLY_LIMIT_USD = float(os.environ.get("MONTHLY_LIMIT_USD", "20"))

# Where the per-person ledgers live, beside the saved facts.
SPEND_FOLDER = Path(os.environ.get(
    "SPEND_FOLDER", PROJECT_ROOT / "data" / "spend"))

# Shared with whatever sits in front of the HTTP service. When set, every
# request must carry it in X-Gateway-Key or be refused - which is what stops
# "X-User-Id: someone-else" from working for anyone who can reach the port.
# Unset leaves the service open, which is only safe on a machine where nothing
# else can reach it.
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "")

# Working hours, used when looking for free time.
WORK_START_HOUR = int(os.environ.get("WORK_START_HOUR", 9))
WORK_END_HOUR = int(os.environ.get("WORK_END_HOUR", 17))

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# How hard the model thinks before answering, and the main cost lever: thinking
# is billed as output tokens, the expensive side. "low" suits this project -
# Python does every date calculation, so the model is choosing a tool and
# phrasing a result, not reasoning through anything. Raise it to "medium" or
# "high" if answers start missing the point.
MODEL_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "low")

# ---------------------------------------------------------------------------
# Google Calendar. None of this belongs in version control.
#
# GOOGLE_ICS_URL is a credential, not a location: anyone holding it can read
# the whole calendar forever without logging in. .gitignore blocks it via .env.
# ---------------------------------------------------------------------------

# Settings -> your calendar -> Integrate calendar -> "Secret address in iCal format"
GOOGLE_ICS_URL = os.environ.get("GOOGLE_ICS_URL", "")

# Where `main.py cache` writes a local copy, so you can work offline.
CACHED_ICS_FILE = Path(os.environ.get(
    "CACHED_ICS_FILE", PROJECT_ROOT / "data" / "google_cache.ics"))

# OAuth, for the authorized API path that the read-only iCal feed cannot cover.
# credentials.json identifies this application and is downloaded from the Google
# Cloud console. token.json is written on first sign-in and holds the access and
# refresh tokens - it is a live credential, not a cache. Both are git-ignored.
CREDENTIALS_FILE = Path(os.environ.get(
    "CREDENTIALS_FILE", PROJECT_ROOT / "credentials.json"))
TOKEN_FILE = Path(os.environ.get(
    "TOKEN_FILE", PROJECT_ROOT / "token.json"))

# Hand the token back to Google and delete it when a signed-in session ends, so
# nothing usable is left on disk. The cost is a browser consent on every single
# run - there is no saved token to reuse, by design. Set REVOKE_ON_EXIT=0 to
# keep the token between sessions instead.
REVOKE_ON_EXIT = os.environ.get("REVOKE_ON_EXIT", "1").lower() not in {
    "0", "false", "no"}

# How long to wait for the browser to come back during sign-in. Declining the
# consent screen redirects with an error, but abandoning it - "Back to safety"
# on the unverified-app warning, or just closing the tab - sends nothing, and
# without a deadline the sign-in waits forever with no output. Long enough to
# read a warning, pick an account, and consent.
AUTH_TIMEOUT_SECONDS = int(os.environ.get("AUTH_TIMEOUT_SECONDS", 120))