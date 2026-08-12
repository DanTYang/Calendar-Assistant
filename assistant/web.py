"""An HTTP interface to the same assistant the terminal talks to.

Nothing here answers a question. Every endpoint hands off to the functions the
CLI already uses, and the only work done in this module is turning a request
into their arguments and their result into JSON. If an answer is wrong, it is
wrong in `queries` or `agent`, not here - which is the point of keeping this
layer thin.

Who is asking arrives in the `X-User-Id` header, and everything remembered -
this conversation, and the facts saved from it - is kept per person. This
service does not authenticate that header, because it cannot: it never sees a
password or a Google consent screen. What it can do is refuse to answer anyone
who is not the service allowed to ask.

Set `GATEWAY_SECRET` and every request must carry it in `X-Gateway-Key`.
Without that, `X-User-Id` is a claim anyone able to reach this process can
make. Leaving the secret unset keeps the terminal and single-user setups
working unchanged, and is only safe while nothing else can reach the port.

Access to the calendar arrives the same way, in `X-Google-Token`: an access
token somebody else obtained and keeps renewed. Nothing here reads a token from
disk, so the process is not tied to one account, and no token outlives the
request that carried it.

Each person's calendar is fetched with their own token and held briefly, since
re-reading it would mean a round trip to Google before every question. The
cache is keyed by person, which is the part that matters - the alternative is
answering one person's question from another's calendar.
"""

import hmac
import time
from datetime import timedelta

from flask import Flask, jsonify, request

import config
from assistant import agent, memory, queries, recurrence, search


# How long a fetched calendar is reused before going back to Google. Short
# enough that a change made elsewhere shows up quickly, long enough that a
# conversation is not a round trip per question.
CALENDAR_TTL_SECONDS = 60


class BadRequest(Exception):
    """The caller asked for something that does not make sense."""


class NotAllowed(Exception):
    """The caller did not prove it is the service allowed to ask."""


class Upstream(Exception):
    """Google or the model failed us, rather than the caller getting it wrong."""


def _int_arg(name, default):
    """Read a whole-number query parameter, refusing anything else.

    Flask hands back strings, and `int("soon")` raises a ValueError that would
    otherwise surface as a 500 - a bug on our side rather than a bad request.
    """
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise BadRequest(f"{name} must be a whole number, not {raw!r}")


def create_app(source="file", occurrences=None, chunks=None):
    """Build the application.

    `occurrences` and `chunks` can be passed in already loaded, which is what
    the tests do: it keeps them off the network and lets them work against the
    same fixtures every other test uses.
    """
    if chunks is None:
        chunks = search.build_chunks(search.load_notes(config.NOTES_FOLDER))

    if occurrences is None:
        if source == agent.WRITABLE_SOURCE:
            # Nothing to load. Serving the API source means every caller brings
            # their own calendar, so there is no single one to read at startup -
            # and reading one here would mean this process needed a Google
            # account of its own, which is exactly what the gateway removes.
            occurrences = []
        else:
            from assistant.main import load_events
            events = load_events(source)
            window = timedelta(days=365)
            occurrences = recurrence.expand_all(
                events, config.NOW - window, config.NOW + window)

    app = Flask(__name__)
    app.config["SOURCE"] = source

    # One per person, kept for the life of the process so a follow-up question
    # still has the conversation behind it. A real deployment would want these
    # to expire; nothing here grows without bound in a single session.
    remembered = {}

    def _memory_for(user_id):
        if user_id not in remembered:
            remembered[user_id] = memory.UserMemory(user_id)
        return remembered[user_id]

    def _caller():
        """Who this request is for, or None for the single shared identity."""
        user_id = (request.headers.get("X-User-Id") or "").strip()
        return user_id or None

    def _credentials():
        """The caller's access token, if one came with the request."""
        token = (request.headers.get("X-Google-Token") or "").strip()
        if not token:
            return None
        from assistant import google_api
        return google_api.credentials_from_token(token)

    # user id -> (occurrences, when it was loaded). Only the api source is per
    # person; a file or a feed is one calendar however many people ask about it.
    calendars = {}

    def _occurrences_for(user_id, credentials):
        if credentials is None or app.config["SOURCE"] != agent.WRITABLE_SOURCE:
            return occurrences

        cached = calendars.get(user_id)
        if cached and time.monotonic() - cached[1] < CALENDAR_TTL_SECONDS:
            return cached[0]

        from assistant import google_api
        events = google_api.load_from_api(credentials=credentials)
        window = timedelta(days=365)
        theirs = recurrence.expand_all(
            events, config.NOW - window, config.NOW + window)
        calendars[user_id] = (theirs, time.monotonic())
        return theirs

    def _run(work):
        """Call one of the query functions, translating what it can raise.

        `parse_when` raises ValueError carrying the list of phrases it accepts,
        which is exactly what a caller needs to fix their request - so it
        becomes the body of a 400 rather than being swallowed.
        """
        try:
            return work()
        except ValueError as error:
            raise BadRequest(str(error)) from error

    @app.before_request
    def _check_caller():
        """Refuse anyone who cannot prove they are the gateway.

        Health is exempt so a load balancer or a person can still ask whether
        the process is alive without holding a credential.
        """
        if not config.GATEWAY_SECRET or request.path == "/health":
            return None
        offered = request.headers.get("X-Gateway-Key", "")
        # Compared in constant time: a plain != leaks where two secrets first
        # differ, one request at a time.
        if not hmac.compare_digest(offered, config.GATEWAY_SECRET):
            raise NotAllowed("this service is not open to direct callers")
        return None

    @app.errorhandler(NotAllowed)
    def _not_allowed(error):
        return jsonify({"error": str(error)}), 401

    @app.errorhandler(BadRequest)
    def _bad_request(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(Upstream)
    def _upstream(error):
        # 502 rather than 500: the request was fine, something we depend on
        # was not. The distinction is what tells a caller whether retrying is
        # worth anything.
        return jsonify({"error": str(error)}), 502

    @app.errorhandler(Exception)
    def _unexpected(error):
        # A bug on our side. The message is not echoed back, because it is not
        # written for anyone but us and may name internals.
        app.logger.exception("unhandled error")
        return jsonify({"error": "internal error"}), 500

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "source": app.config["SOURCE"],
            "occurrences": len(occurrences),
            "note_chunks": len(chunks),
            # Writing needs a calendar this process can actually reach.
            "can_write": app.config["SOURCE"] == agent.WRITABLE_SOURCE,
            "known_users": len(remembered),
        })

    @app.get("/events")
    def events():
        when = request.args.get("when", "this week")
        return jsonify({
            "when": when,
            "text": _run(lambda: queries.find_events(
                occurrences, when,
                person=request.args.get("person", ""),
                contains=request.args.get("contains", ""))),
        })

    @app.get("/free-time")
    def free_time():
        when = request.args.get("when", "this week")
        minutes = _int_arg("duration_minutes", 30)
        return jsonify({
            "when": when,
            "duration_minutes": minutes,
            "text": _run(lambda: queries.find_free_time(
                occurrences, when, duration_minutes=minutes)),
        })

    @app.get("/birthdays")
    def birthdays():
        days = _int_arg("within_days", 30)
        return jsonify({
            "within_days": days,
            "text": _run(lambda: queries.upcoming_birthdays(
                occurrences, config.NOW, within_days=days)),
        })

    @app.get("/notes")
    def notes():
        query = request.args.get("query", "").strip()
        if not query:
            raise BadRequest("query is required")
        return jsonify({
            "query": query,
            "text": _run(lambda: search.search_notes(chunks, query)),
        })

    @app.get("/history")
    def history():
        """What this caller has said so far, and what was answered.

        The conversation lives here rather than in the page, so a reload shows
        what actually happened instead of an empty window in front of an
        assistant that remembers everything.
        """
        return jsonify({"turns": _memory_for(_caller()).transcript()})

    @app.post("/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            raise BadRequest("message is required")

        # The same trace the terminal prints before each answer. A caller has
        # no other way to see which tool ran, and for anything that changed the
        # calendar that is worth knowing.
        user_id = _caller()
        credentials = _credentials()
        if credentials is None and app.config["SOURCE"] == agent.WRITABLE_SOURCE:
            # Better said plainly than answered from an empty calendar, which
            # reads as "you have nothing scheduled".
            raise BadRequest(
                "this service is serving the Google Calendar API, so a request "
                "must carry the caller's access token in X-Google-Token")
        used = []
        try:
            answer = agent.ask(
                message, _occurrences_for(user_id, credentials), chunks,
                _memory_for(user_id),
                on_tool_call=lambda name, args: used.append(
                    {"tool": name, "arguments": args}),
                source=app.config["SOURCE"], credentials=credentials)
        except Exception as error:
            # A tool that fails returns its message to the model rather than
            # raising, so reaching here means the model itself was unreachable.
            raise Upstream(f"the assistant could not answer: {error}") from error

        return jsonify({"answer": answer, "tools_used": used,
                        "user": user_id})

    return app


def main(source="file", host="127.0.0.1", port=5000):
    """Run a development server.

    Bound to the loopback address on purpose. There is no authentication yet,
    so anything that can reach this can read the calendar and, with
    `--source api`, change it.
    """
    app = create_app(source)
    print(f"Serving {source!r} on http://{host}:{port}  (no authentication)")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
