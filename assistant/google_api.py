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
from assistant import ics_parser, queries, recurrence


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


def signed_in_account():
    """Which calendar this token actually writes to.

    Worth showing rather than assuming. The account is chosen in a browser,
    away from the terminal, and every writing tool acts on "primary" - so a
    session pointed at the wrong calendar looks exactly like one pointed at the
    right calendar until something lands in it.

    Read from the envelope of an events listing rather than from
    `calendars().get()`, which needs a broader scope than `calendar.events` and
    is refused with "insufficient authentication scopes". A primary calendar's
    `summary` is the account's address, which is exactly the thing worth
    printing - and it costs no extra permission.
    """
    try:
        listing = get_service().events().list(
            calendarId="primary", maxResults=1).execute()
    except HttpError as error:
        raise AuthError(_explain_http_error(error)) from error
    return listing.get("summary", "unknown"), listing.get("timeZone", "")


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


def _parse_clock(text):
    """Turn "14:30" into (14, 30). Raises with advice the model can act on."""
    parts = str(text).strip().split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(
            f"I do not understand the time {text!r}. Use 24-hour HH:MM, "
            "such as '09:00' or '14:30'.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"{text!r} is not a real time of day.")
    return hour, minute


def create_event(occurrences, summary, when, start_time=None,
                 duration_minutes=60, location="", description="",
                 confirm=False):
    """Add an event to the calendar, in two calls.

    Called without `confirm` this writes nothing: it resolves the phrase, works
    out the exact times, and hands back a description for the user to approve.
    Called again with `confirm=True` it performs the insert. Splitting it this
    way is what keeps a misheard request from silently landing on a real
    calendar - and it works over any transport, unlike a tool that tries to
    prompt on stdin.

    The model supplies the phrase and the clock time separately and resolves
    neither. `parse_when` decides what "thursday" means, exactly as it does for
    reading, so the rule that the model never does date arithmetic survives
    contact with writing.

    `occurrences` is the live list the session is answering from. A created
    event is added to it here, because an assistant that cannot see what it
    just did is worse than one that cannot write at all.
    """
    day_start, day_end = queries.parse_when(when, config.NOW)

    # "next week" resolves fine and means nothing here: it is seven candidate
    # days, and guessing one is exactly the failure this project avoids.
    if day_end - day_start > timedelta(days=1):
        return (f"{when!r} covers more than one day, so I cannot tell which "
                "day you mean. Give a single day - 'tomorrow', 'thursday', or "
                "a date like '2026-08-15'.")

    event = ics_parser.new_event()
    event["summary"] = summary
    event["location"] = location
    event["description"] = description
    event["all_day"] = start_time is None

    if event["all_day"]:
        event["start"] = day_start
        event["end"] = day_start + timedelta(days=1)
    else:
        hour, minute = _parse_clock(start_time)
        event["start"] = day_start.replace(hour=hour, minute=minute)
        event["end"] = event["start"] + timedelta(minutes=duration_minutes)

    # Phase one. Rendered with the same formatter every other event goes
    # through, so what is approved looks like what will later be read back.
    if not confirm:
        return ("About to create:\n\n"
                f"  {queries.format_event(event)}\n\n"
                "Nothing has been created yet. Show this to the user and ask "
                "them to confirm. If they agree, call create_event again with "
                "the same arguments and confirm=true.")

    zone = str(config.TIMEZONE)
    if event["all_day"]:
        # All-day events are dates, and the end is the day after - the same
        # half-open convention the .ics format uses.
        when_fields = {
            "start": {"date": event["start"].date().isoformat()},
            "end": {"date": event["end"].date().isoformat()},
        }
    else:
        # Naive timestamps plus an explicit zone: Google resolves them, so no
        # offset arithmetic happens here.
        when_fields = {
            "start": {"dateTime": event["start"].isoformat(), "timeZone": zone},
            "end": {"dateTime": event["end"].isoformat(), "timeZone": zone},
        }

    body = {"summary": summary, **when_fields}
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    try:
        created = get_service().events().insert(
            calendarId="primary", body=body).execute()
    except HttpError as error:
        raise AuthError(_explain_http_error(error)) from error

    # Read back from what Google returned rather than from what was sent. The
    # response is the event as it now exists, which is the only version worth
    # trusting - it may differ from the request.
    stored = _to_event(created)
    if stored is not None:
        window = timedelta(days=365)
        occurrences.extend(recurrence.expand_event(
            stored, config.NOW - window, config.NOW + window))
        occurrences.sort(key=lambda occurrence: occurrence["start"])

    return f"Created:\n\n  {queries.format_event(stored or event)}"


def _resolve_one(occurrences, summary, when, verb):
    """Find the single event a title and a day refer to.

    Returns `(occurrence, None)` on a clean hit, or `(None, message)` when the
    request does not name exactly one thing. Both writing tools need this and
    both must refuse rather than guess: picking the wrong event destroys
    something either way.

    `verb` only shapes the wording ("deleted nothing", "changed nothing").
    """
    start, end = queries.parse_when(when, config.NOW)
    wanted = summary.strip().lower()

    # Overlap, not containment - the same rule the rest of the project matches
    # events with.
    matches = [
        occurrence for occurrence in occurrences
        if occurrence["start"] < end and occurrence["end"] > start
        and wanted in occurrence["summary"].lower()
    ]

    if not matches:
        return None, (f"No event matching {summary!r} in {when!r}. Try "
                      "find_events first to see what is actually there.")

    if len(matches) > 1:
        listed = "\n".join(f"  {queries.format_event(m)}" for m in matches)
        return None, (f"{len(matches)} events match {summary!r} in {when!r}, "
                      f"so I have {verb} nothing:\n\n{listed}\n\n"
                      "Ask the user which one they mean, then use a more "
                      "specific title or a single date.")

    target = matches[0]
    if target["rrule"]:
        return None, (f"{target['summary']!r} repeats. Acting on it would "
                      "affect the entire series rather than this one "
                      f"occurrence, so I have {verb} nothing. Changing a "
                      "single occurrence of a repeating event is not "
                      "supported yet.")

    return target, None


def delete_event(occurrences, summary, when, confirm=False):
    """Remove an event from the calendar, in two calls like `create_event`."""
    target, problem = _resolve_one(occurrences, summary, when, "deleted")
    if problem:
        return problem

    if not confirm:
        return ("About to delete:\n\n"
                f"  {queries.format_event(target)}\n\n"
                "This cannot be undone. Nothing has been deleted yet. Show "
                "this to the user, and only if they agree, call delete_event "
                "again with the same arguments and confirm=true.")

    try:
        get_service().events().delete(
            calendarId="primary", eventId=target["uid"]).execute()
    except HttpError as error:
        if getattr(error.resp, "status", None) in (404, 410):
            # Already gone. Drop it locally so the session stops offering it.
            occurrences[:] = [o for o in occurrences
                              if o["uid"] != target["uid"]]
            return (f"{target['summary']!r} was already gone from the "
                    "calendar. Removed it from this session.")
        raise AuthError(_explain_http_error(error)) from error

    occurrences[:] = [o for o in occurrences if o["uid"] != target["uid"]]
    return f"Deleted:\n\n  {queries.format_event(target)}"


def update_event(occurrences, summary, when, new_when=None, new_start_time=None,
                 new_duration_minutes=None, new_summary=None,
                 new_location=None, confirm=False):
    """Change an existing event, sending only the fields that differ.

    Uses Google's `patch`, which leaves everything it is not told about alone.
    The obvious alternative - delete the old event and create a replacement -
    looks equivalent and is not: it mints a new id, emails a cancellation
    followed by a fresh invitation so every RSVP is lost, drops the meeting
    link and every other field this project does not model, and is not atomic.
    A dropped connection halfway through would destroy the event rather than
    leave it unchanged.

    Anything not named keeps its current value, so "move it to Friday" holds
    the time and "make it 3pm" holds the day.
    """
    target, problem = _resolve_one(occurrences, summary, when, "changed")
    if problem:
        return problem

    nothing_asked = all(field is None for field in (
        new_when, new_start_time, new_duration_minutes, new_summary,
        new_location))
    if nothing_asked:
        return ("Nothing to change. Say what should be different - a new day, "
                "a new time, a new length, a new title, or a new location.")

    # Start from what the event is now, then apply only what was asked for.
    moved = dict(target)

    if new_summary is not None:
        moved["summary"] = new_summary
    if new_location is not None:
        moved["location"] = new_location

    day = target["start"].date()
    if new_when is not None:
        day_start, day_end = queries.parse_when(new_when, config.NOW)
        if day_end - day_start > timedelta(days=1):
            return (f"{new_when!r} covers more than one day, so I cannot tell "
                    "which day you mean. Give a single day.")
        day = day_start.date()

    length = target["end"] - target["start"]
    if new_duration_minutes is not None:
        length = timedelta(minutes=new_duration_minutes)

    if new_start_time is not None:
        hour, minute = _parse_clock(new_start_time)
        moved["all_day"] = False
        moved["start"] = datetime(day.year, day.month, day.day, hour, minute)
        moved["end"] = moved["start"] + length
    elif target["all_day"]:
        moved["start"] = datetime(day.year, day.month, day.day)
        moved["end"] = moved["start"] + max(length, timedelta(days=1))
    else:
        moved["start"] = target["start"].replace(
            year=day.year, month=day.month, day=day.day)
        moved["end"] = moved["start"] + length

    if not confirm:
        return ("About to change:\n\n"
                f"  from  {queries.format_event(target)}\n"
                f"    to  {queries.format_event(moved)}\n\n"
                "Nothing has been changed yet. Show this to the user and only "
                "if they agree, call update_event again with the same "
                "arguments and confirm=true.")

    # Only what actually differs. Everything unmentioned - the meeting link,
    # reminders, guest permissions, colour - survives untouched.
    body = {}
    if moved["summary"] != target["summary"]:
        body["summary"] = moved["summary"]
    if moved["location"] != target["location"]:
        body["location"] = moved["location"]

    if (moved["start"], moved["end"], moved["all_day"]) != (
            target["start"], target["end"], target["all_day"]):
        zone = str(config.TIMEZONE)
        if moved["all_day"]:
            body["start"] = {"date": moved["start"].date().isoformat()}
            body["end"] = {"date": moved["end"].date().isoformat()}
        else:
            body["start"] = {"dateTime": moved["start"].isoformat(),
                             "timeZone": zone}
            body["end"] = {"dateTime": moved["end"].isoformat(),
                           "timeZone": zone}

    if not body:
        return "That is already how the event looks. Nothing to change."

    try:
        patched = get_service().events().patch(
            calendarId="primary", eventId=target["uid"], body=body).execute()
    except HttpError as error:
        raise AuthError(_explain_http_error(error)) from error

    # Replace the old occurrences with whatever Google says the event is now.
    stored = _to_event(patched)
    occurrences[:] = [o for o in occurrences if o["uid"] != target["uid"]]
    if stored is not None:
        window = timedelta(days=365)
        occurrences.extend(recurrence.expand_event(
            stored, config.NOW - window, config.NOW + window))
        occurrences.sort(key=lambda occurrence: occurrence["start"])

    return ("Changed:\n\n"
            f"  from  {queries.format_event(target)}\n"
            f"    to  {queries.format_event(stored or moved)}")


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
