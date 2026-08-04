"""Load events from a real Google Calendar via its secret iCal address.

Google publishes every calendar at a private address ending in `/basic.ics`.
Download that and the existing `.ics` parser handles the rest: no OAuth, no
cloud project, and no new event shape for anything downstream to learn.

Notice what is *not* here. Nothing below `parse_events` changes to support a
remote calendar, because the bytes arriving over HTTP are the same iCalendar
text a local file would contain. The event dictionary stays the interface.
"""

import urllib.request
from pathlib import Path

import config
from assistant.ics_parser import parse_events


def load_from_url(url=None, timeout=30):
    """Download a calendar's secret iCal address and parse it.

    The URL is a credential: anyone holding it can read the whole calendar
    forever, with no login. Keep it in the GOOGLE_ICS_URL environment variable,
    never in the source.
    """
    url = url or config.GOOGLE_ICS_URL
    if not url:
        raise ValueError(
            "No calendar URL. In Google Calendar open Settings -> your calendar "
            "-> Integrate calendar -> 'Secret address in iCal format', then set "
            "it as GOOGLE_ICS_URL.")

    with urllib.request.urlopen(url, timeout=timeout) as response:
        text = response.read().decode("utf-8")
    return parse_events(text)


def cache_from_url(url=None, path=None):
    """Save a local copy of the downloaded calendar so you can work offline."""
    url = url or config.GOOGLE_ICS_URL
    path = Path(path or config.CACHED_ICS_FILE)

    with urllib.request.urlopen(url, timeout=30) as response:
        text = response.read().decode("utf-8")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
