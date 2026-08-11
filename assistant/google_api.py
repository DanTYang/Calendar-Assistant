"""Authorized access to Google Calendar through the API, using OAuth.

This is the second Google path, and it exists for something the iCal feed
cannot do: write. `google_calendar.py` downloads the secret iCal address, which
is a read-only export and is cached by Google - no event can be created through
it, and it can lag behind changes made elsewhere. That module stays as it is;
this one sits beside it.

The price is a Google Cloud project and two files on disk. `credentials.json`
identifies this application and is downloaded from the console. `token.json` is
written after the first sign-in and holds the tokens issued to it. Both are
git-ignored, and `token.json` is a live credential rather than a cache: anyone
holding it can read and edit the calendar it was granted.

Nothing here converts Google's JSON into the event dictionary the rest of the
project speaks. That boundary is deliberate and comes next - for now this
module only proves the authorization works, so a failure here means the sign-in
is wrong and nothing else.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

import config
from assistant import ics_parser


class AuthError(Exception):
    """Signing in did not finish.

    Raised instead of letting a library exception escape, because the caller
    shows this message to a person: every one of them says what went wrong and
    what to do about it. `main.py` catches this to offer another attempt.
    """

# Narrow on purpose: this grants reading and writing events, and nothing else -
# not calendar settings, not sharing rules, not the list of calendars.
#
# Changing this list does not change an existing token.json. The saved token
# keeps the permissions it was issued with, so the new ones fail at the point of
# use rather than at sign-in. Delete token.json and sign in again.
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Where a token is handed back to Google to be invalidated. See `sign_out`.
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def get_credentials():
    """Return valid credentials, signing in through the browser if needed.

    Three cases, in order: a saved token still good, a saved token that expired
    but carries a refresh token, and nothing usable. Only the last opens a
    browser, which is why a second run should be silent - if it is not, the
    token was never written.
    """
    creds = None
    if config.TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(config.TOKEN_FILE), SCOPES)
        except (ValueError, KeyError, json.JSONDecodeError):
            # A half-written or hand-edited token is not worth diagnosing.
            # Discard it and sign in again, which rewrites the file anyway.
            creds = None

    # Nothing to do, and nothing worth rewriting. Returning here is what keeps
    # the file untouched on the common path.
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # A refresh token can be revoked, and expires after seven days
            # while the consent screen is still in "Testing". Dropping it turns
            # a crash into one more browser prompt.
            creds = None

    if not creds or not creds.valid:
        creds = _sign_in()

    config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _sign_in():
    """Run the browser consent flow, reporting failures as `AuthError`.

    Everything this can raise is something a person can act on - a missing
    file, a declined consent screen - so none of it should reach the terminal
    as a stack trace.
    """
    if not config.CREDENTIALS_FILE.exists():
        raise AuthError(
            f"No {config.CREDENTIALS_FILE.name} in {config.PROJECT_ROOT}.\n"
            "  In the Google Cloud console: enable the Calendar API, configure "
            "the consent screen, create an OAuth client ID of type 'Desktop "
            "app', then download the JSON and save it under that name.")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.CREDENTIALS_FILE), SCOPES)
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise AuthError(
            f"{config.CREDENTIALS_FILE.name} is not a readable OAuth client "
            f"file ({error}).\n"
            "  Download it again from the console, choosing 'Desktop app' - a "
            "'Web application' client will not work here.") from error

    print(f"Waiting for the browser (up to {config.AUTH_TIMEOUT_SECONDS}s)...")
    try:
        # port=0 takes any free port. The flow runs a throwaway local server to
        # catch Google's redirect, which is why a desktop client needs no public
        # address: the authorization code arrives from your own browser.
        #
        # The timeout is what makes abandonment detectable. Declining the
        # consent screen redirects back with an error, but walking away -
        # closing the tab, or "Back to safety" on the unverified-app warning -
        # sends nothing at all, and without a deadline this waits forever.
        return flow.run_local_server(
            port=0, timeout_seconds=config.AUTH_TIMEOUT_SECONDS)
    except AccessDeniedError as error:
        raise AuthError(
            "You declined the consent screen, so nothing was authorized.\n"
            "  The 'app is not verified' warning is expected while the consent "
            "screen is in Testing - choose Advanced, then continue.") from error
    except WSGITimeoutError as error:
        raise AuthError(
            f"The browser never came back "
            f"(waited {config.AUTH_TIMEOUT_SECONDS}s).\n"
            "  If you chose 'Back to safety' on the unverified-app warning, "
            "that leaves without answering: pick Advanced, then 'Go to ... "
            "(unsafe)' instead. Closing the tab has the same effect.") from error
    except Exception as error:
        raise AuthError(
            f"The browser sign-in did not finish: "
            f"{type(error).__name__}: {error}") from error


def _naive(stamp):
    """One of Google's timestamps as a naive datetime in the assumed zone.

    Timed events arrive with an offset ("2026-08-17T11:40:00-04:00"), all-day
    events as a bare date. Everything downstream of here is naive, so the
    offset is resolved against `config.TIMEZONE` and then dropped. Converting
    rather than truncating matters: an event Google reports in another zone
    still lands at the right local time.
    """
    value = datetime.fromisoformat(stamp)
    if value.tzinfo is not None:
        value = value.astimezone(config.TIMEZONE).replace(tzinfo=None)
    return value


def _rfc3339(when):
    """A naive local datetime as the offset-bearing timestamp the API wants."""
    return when.replace(tzinfo=config.TIMEZONE).isoformat()


def _to_event(item):
    """Convert one API event into the dictionary the rest of the project reads.

    Returns None for records that have no place in that shape: cancelled
    events, and the separate entries Google writes for a single rescheduled
    instance of a recurring series. Skipping the latter is not an oversight -
    it is the same limitation the `.ics` parser has with `RECURRENCE-ID`, kept
    deliberately so both sources behave alike.
    """
    if item.get("status") == "cancelled" or item.get("recurringEventId"):
        return None

    start = item.get("start") or {}
    end = item.get("end") or {}
    if not (start.get("dateTime") or start.get("date")):
        return None  # No start: malformed, and `parse_events` skips these too.

    event = ics_parser.new_event()
    # Google's own iCal export publishes this id as "<id>@google.com", so the
    # url and api sources agree on identity bar the suffix - and this is the
    # form the API itself needs to edit the event later.
    event["uid"] = item.get("id", "")
    event["summary"] = item.get("summary", "")
    event["description"] = item.get("description", "")
    event["location"] = item.get("location", "")
    event["all_day"] = "date" in start

    event["start"] = _naive(start.get("dateTime") or start["date"])
    if end.get("dateTime") or end.get("date"):
        event["end"] = _naive(end.get("dateTime") or end["date"])

    for line in item.get("recurrence", []):
        # These arrive as literal iCalendar lines - "RRULE:FREQ=WEEKLY;BYDAY=MO",
        # "EXDATE;TZID=America/New_York:20260817T210000" - which is precisely
        # what ics_parser already knows how to read. The name may carry
        # parameters, so split on the first colon rather than matching exactly.
        name, _, value = line.partition(":")
        name = name.upper()
        if name.startswith("RRULE"):
            event["rrule"] = ics_parser.strip_until_marker(value)
        elif name.startswith("EXDATE"):
            event["exdates"].extend(
                ics_parser.parse_datetime(part)
                for part in ics_parser.split_escaped(value))

    event["attendees"] = [
        person.get("displayName") or person.get("email", "")
        for person in item.get("attendees", [])
    ]

    # Borrowed rather than reimplemented: what a missing end time means should
    # have one definition, not two that can drift apart.
    return ics_parser._finish(event)


def load_from_api(horizon_days=400):
    """Load events from the Calendar API as event dictionaries.

    The third source, beside a local file and the iCal feed, and the only one
    that reflects a change the moment it is made - which is what makes it the
    right one to read back an event this assistant just created.

    Recurring events are fetched as rules rather than instances
    (`singleEvents=False`). Google would happily expand them, but that routes
    around `recurrence.py`, the one module here that understands RRULE. Its
    `recurrence` field is iCalendar text, so the rules survive the trip intact.
    """
    service = get_service()
    window = timedelta(days=horizon_days)
    params = {
        "calendarId": "primary",
        "timeMin": _rfc3339(config.NOW - window),
        "timeMax": _rfc3339(config.NOW + window),
        # No orderBy: the API rejects sorting by start time unless it is the
        # one expanding the series, and `expand_all` sorts afterwards anyway.
        "singleEvents": False,
        "maxResults": 250,
        "showDeleted": False,
    }

    events = []
    page_token = None
    while True:
        try:
            response = service.events().list(
                **params, pageToken=page_token).execute()
        except HttpError as error:
            raise AuthError(_explain_http_error(error)) from error

        for item in response.get("items", []):
            event = _to_event(item)
            if event is not None:
                events.append(event)

        # A busy calendar runs past one page, and a silently truncated calendar
        # is worse than a slow one.
        page_token = response.get("nextPageToken")
        if not page_token:
            return events


def sign_out():
    """Revoke the saved token with Google and delete it from disk.

    Deleting the file alone would be enough to force a new sign-in next time,
    but it would leave the grant alive on Google's side: the file is gone, the
    access is not. Revoking first closes that gap.

    The file is removed either way. A revoke that fails because the laptop is
    offline must not be the reason a live credential stays on disk - and a
    token Google has already forgotten is not worth keeping either.

    Returns True if Google confirmed the revocation.
    """
    if not config.TOKEN_FILE.exists():
        return False

    token = None
    try:
        saved = json.loads(config.TOKEN_FILE.read_text(encoding="utf-8"))
        # Revoking either token drops the whole grant; the refresh token is the
        # one that would otherwise outlive this session.
        token = saved.get("refresh_token") or saved.get("token")
    except (ValueError, OSError):
        pass  # Unreadable: nothing to revoke, but still worth deleting.

    revoked = False
    if token:
        body = urllib.parse.urlencode({"token": token}).encode("utf-8")
        request = urllib.request.Request(REVOKE_URL, data=body)
        try:
            with urllib.request.urlopen(request, timeout=10):
                revoked = True
        except urllib.error.URLError:
            # Offline, or the token was already revoked. Neither is worth
            # interrupting shutdown over.
            revoked = False

    config.TOKEN_FILE.unlink(missing_ok=True)
    return revoked


def _explain_http_error(error):
    """Turn a Google API error into a sentence worth showing someone.

    Only the two failures that actually happen during setup are named; anything
    else falls through with its status, which is more useful than a guess.
    """
    status = getattr(error.resp, "status", None)
    text = str(error)

    if status == 403 and "has not been used" in text:
        return (
            "The Calendar API is not enabled on this Google Cloud project.\n"
            "  Enable it in the console, then sign in again. Check the project "
            "selector too - it is easy to enable the API on one project while "
            "the OAuth client lives on another.")
    if status == 401:
        return (
            "Google rejected the saved token.\n"
            f"  Delete {config.TOKEN_FILE.name} and sign in again.")
    return f"Google refused the request (HTTP {status}): {text}"


def get_service():
    """Build the Calendar API client, signing in first if necessary."""
    return build("calendar", "v3", credentials=get_credentials())


def list_upcoming(max_results=5):
    """Return `(when, summary)` pairs for the next few events, soonest first.

    This exists to prove the authorization works end to end, and is all that
    `login` needs. It deliberately returns strings rather than event
    dictionaries: converting Google's JSON to the shape `ics_parser.new_event()`
    defines is a separate job, and doing both at once would make a failure here
    ambiguous.
    """
    service = get_service()

    # RFC3339 with an offset, which the API requires - a naive datetime is
    # rejected. The rest of the project is naive by design, so this is the one
    # place a timezone is attached, and it goes no further than this call.
    time_min = datetime.now(timezone.utc).isoformat()

    # singleEvents=True has Google expand recurrence for this check, which keeps
    # the proof short. The loader path will not do that: recurrence.py owns
    # expansion, and that is a decision worth making deliberately, not by
    # inheriting a flag from here.
    try:
        response = service.events().list(
            calendarId="primary", timeMin=time_min, maxResults=max_results,
            singleEvents=True, orderBy="startTime").execute()
    except HttpError as error:
        raise AuthError(_explain_http_error(error)) from error

    upcoming = []
    for item in response.get("items", []):
        # Timed events carry "dateTime"; all-day events carry "date" instead,
        # never both. Birthdays are the first thing to hit this.
        start = item["start"].get("dateTime") or item["start"].get("date", "")
        upcoming.append((start, item.get("summary", "(no title)")))
    return upcoming
