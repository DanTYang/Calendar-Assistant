"""Settings, all overridable from the environment.

Secrets - the API key, the calendar URL - belong in a `.env` file next to this
one, which is git-ignored. `.env.example` shows the shape without the values.
Real environment variables win over anything in `.env`, so CI and containers
work without one.
"""

import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # optional - the app runs fine on real env vars alone
    pass

CALENDAR_FILE = Path(os.environ.get(
    "CALENDAR_FILE", PROJECT_ROOT / "data" / "sample_calendar.ics"))
NOTES_FOLDER = Path(os.environ.get(
    "NOTES_FOLDER", PROJECT_ROOT / "data" / "notes"))
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

# Working hours, used when looking for free time.
WORK_START_HOUR = int(os.environ.get("WORK_START_HOUR", 9))
WORK_END_HOUR = int(os.environ.get("WORK_END_HOUR", 17))

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

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