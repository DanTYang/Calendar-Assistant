"""Load events from a real Google Calendar, via iCal URL or the REST API.

Two routes, both ending in the same place:

  URL  Google publishes every calendar at a private address ending in
       /basic.ics. Download it and the existing .ics parser handles the rest.
       No OAuth, no cloud project.

  API  OAuth 2.0 and a JSON response, which needs converting.

Notice what is *not* here: nothing downstream changes. Google returns a
completely different shape, and the only new code is `convert_event`, which
produces the same dictionary `ics_parser.new_event()` defines. Recurrence
expansion, querying, search and the agent loop are all untouched. That is what
makes the event dictionary an interface rather than just a data structure.

Requires `pip install google-api-python-client google-auth-oauthlib` for the
API route only.
"""

import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import config
from assistant.ics_parser import DEFAULT_ALL_DAY_DURATION, DEFAULT_DURATION, parse_events

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Google caps a page at 2500. Personal calendars usually fit in one or two.
PAGE_SIZE = 2500

INSTALL_HINT = ("The API route needs two extra packages:\n"
                "    pip install google-api-python-client google-auth-oauthlib")


# ---------------------------------------------------------------------------
# Route 1: the secret iCal URL
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Route 2: the Calendar API
# ---------------------------------------------------------------------------

def _credentials():
    """Load saved OAuth credentials, refreshing or re-authorising as needed.

    The first run opens a browser. After that `token.json` is reused. While the
    OAuth consent screen is in "Testing" status Google expires refresh tokens
    after seven days, so expect to re-authorise weekly until it is published.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as missing:
        raise ImportError(f"{missing}. {INSTALL_HINT}") from missing

    token_file = Path(config.GOOGLE_TOKEN_FILE)
    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            secrets = Path(config.GOOGLE_CREDENTIALS_FILE)
            if not secrets.exists():
                raise FileNotFoundError(
                    f"{secrets} not found. Create an OAuth client ID of type "
                    "'Desktop app' in the Google Cloud console and download it.")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            credentials = flow.run_local_server(port=0)
        token_file.write_text(credentials.to_json())

    return credentials


def fetch_api_events(calendar_id=None, single_events=False):
    """Fetch every event from the API, following pagination to the last page.

    No timeMin/timeMax filter is sent when `single_events` is False. Google
    applies those bounds to the *master* event, so a weekly standup that began
    in 2019 - or a birthday whose DTSTART is a 1993 date - would be filtered
    out before its upcoming instances were ever generated. Fetching everything
    and letting `recurrence.expand_all` do the windowing keeps that logic in
    one place.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError as missing:
        raise ImportError(f"{missing}. {INSTALL_HINT}") from missing

    service = build("calendar", "v3", credentials=_credentials())
    calendar_id = calendar_id or config.GOOGLE_CALENDAR_ID

    request = {"calendarId": calendar_id, "maxResults": PAGE_SIZE,
               "singleEvents": single_events, "showDeleted": False}
    if single_events:
        # Only meaningful once Google is expanding instances for us.
        window = timedelta(days=365)
        request["timeMin"] = (config.NOW - window).isoformat() + "Z"
        request["timeMax"] = (config.NOW + window).isoformat() + "Z"
        request["orderBy"] = "startTime"

    items, page_token = [], None
    while True:
        response = service.events().list(pageToken=page_token, **request).execute()
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items


def load_from_api(calendar_id=None, single_events=False):
    """Fetch and convert a Google calendar into the project's event format."""
    return convert_events(fetch_api_events(calendar_id, single_events))


# ---------------------------------------------------------------------------
# Converting Google's JSON into the project's event dictionary
# ---------------------------------------------------------------------------

def google_datetime(value):
    """Turn a Google start/end object into (datetime, is_all_day).

        {"dateTime": "2026-08-04T08:00:00+01:00"}  ->  (datetime(...8, 0), False)
        {"date": "2026-08-07"}                     ->  (datetime(...0, 0), True)

    The offset is discarded rather than converted, matching the project-wide
    convention that datetimes are naive wall-clock times.
    """
    if not value:
        return None, False
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"]).replace(tzinfo=None), False
    if "date" in value:
        return datetime.fromisoformat(value["date"]), True
    return None, False


def _recurrence_fields(lines):
    """Pull the RRULE string and EXDATE list out of Google's `recurrence` array.

    Google returns raw iCalendar lines here, which is convenient - the RRULE
    goes straight through to `recurrence.expand_event` untouched.
    """
    rrule, exdates = "", []
    for line in lines or []:
        name, _, value = line.partition(":")
        if name.upper().startswith("RRULE"):
            rrule = value
        elif name.upper().startswith("EXDATE"):
            for stamp in value.split(","):
                stamp = stamp.strip().rstrip("Z")
                if stamp:
                    exdates.append(datetime.strptime(
                        stamp, "%Y%m%dT%H%M%S" if "T" in stamp else "%Y%m%d"))
    return rrule, exdates


def convert_event(item):
    """Convert one Google event into the project's event dictionary, or None.

    Returns None for events that should not appear at all: cancelled ones, and
    anything without a usable start.
    """
    if item.get("status") == "cancelled":
        return None

    start, all_day = google_datetime(item.get("start"))
    if start is None:
        return None
    end, _ = google_datetime(item.get("end"))
    if end is None:
        end = start + (DEFAULT_ALL_DAY_DURATION if all_day else DEFAULT_DURATION)

    rrule, exdates = _recurrence_fields(item.get("recurrence"))

    # Google has no CATEGORIES field, but it does tag birthday events, which is
    # what `queries.is_birthday` looks for first.
    categories = ["Birthday"] if item.get("eventType") == "birthday" else []

    return {
        "uid": item.get("id", ""),
        "summary": item.get("summary", "(no title)"),
        "description": item.get("description", ""),
        "location": item.get("location", ""),
        "start": start,
        "end": end,
        "all_day": all_day,
        "rrule": rrule,
        "exdates": exdates,
        # displayName is absent for people who are not in your contacts.
        "attendees": [guest.get("displayName") or guest.get("email", "")
                      for guest in item.get("attendees", [])],
        "categories": categories,
    }


def convert_events(items):
    """Convert a list of Google events, dropping the ones that cannot be used."""
    converted = (convert_event(item) for item in items)
    return [event for event in converted if event]