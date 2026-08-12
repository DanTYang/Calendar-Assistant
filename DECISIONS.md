# Open decisions

Choices made while building that are worth revisiting, and cheap to reverse.
None of these is wrong; each traded something for something, and the trade may
not be the one you want. Kept separate from the README because the README
describes what the project *does* - this is a list of things still up for
argument.

Each entry says what changes if you flip it, and where.

---

## 1. Every run asks for consent again

**Now:** `REVOKE_ON_EXIT=1`. When a signed-in session ends, the token is
revoked with Google and deleted, so the next run opens a browser.

**Bought:** nothing usable is left on disk. A stolen laptop yields no calendar
access.

**Cost:** a browser and three clicks every single time you use it, including
the "app isn't verified" warning.

**Reverse:** `REVOKE_ON_EXIT=0` in `.env`. Nothing else changes.

**Worth reconsidering when** you use the CLI often enough that the prompt
becomes the reason you stop using it.

---

## 2. `login` overrides your `--source`

**Now:** `login` switches the source to `api` unless you passed `--source`
explicitly.

**Bought:** signing in to a real account and then being shown the fictional
sample calendar is absurd, and that is what happened before.

**Cost:** a command quietly does something other than the default.

**Reverse:** delete the `if not source_given` block in `main.py`.

---

## 3. The gateway asks for consent on every sign-in

**Now:** `prompt=consent` in `SecurityConfig`.

**Bought:** Google only issues a refresh token on the consent that first grants
access. Without forcing it, a returning user gets an access token and nothing
to renew it with - and that failure appears an hour later, not at sign-in.

**Cost:** a consent screen every time, even for someone who has approved
before.

**Reverse:** remove the `prompt` query parameter once tokens are stored and
reused across sessions. Keep `access_type=offline`.

---

## 4. The Java gateway lives inside the Python repository

**Now:** `gateway/` sits beside `assistant/`, one git history.

**Bought:** one clone, one place, and the polyglot split is visible rather than
described.

**Cost:** two toolchains in one repository. A Java CI job checks out Python it
does not need, and vice versa.

**Reverse:** `git mv gateway ../gateway` and `git init` there. Easier now than
after either side has history worth keeping separate.

---

## 5. The tests are not published

**Now:** `.gitignore` excludes `tests/` and `pytest.ini`. Inherited, not chosen
by me.

**Cost:** 138 tests do not reach GitHub, including the ones that caught the
four-hour timezone bug, the duplicate occurrence, and the path-traversal case.
Anyone cloning gets none of that, and anyone reading the repository sees no
evidence any of it was tested.

**Reverse:** remove those two lines from `.gitignore`.

**Worth reconsidering** if the repository is ever something you point an
employer at - the tests are the strongest evidence in it.

---

## 6. Location classification errs towards "somewhere to go"

**Now:** `queries.location_kind` returns `place` unless the location names a
video call or a meeting room.

**Bought:** Google Maps resolves "Javits Center" and "Newark EWR" as readily as
a postal address, so refusing anything that is not a full address would refuse
most real events.

**Cost:** an odd location gets an odd link rather than nothing.

**Reverse:** tighten `location_kind` to require something address-shaped. The
tests in `test_queries.py` document the current boundary.

---

## 7. Behaviour specified in prose, not code

**Now:** the system prompt in `agent.py` carries widening, offering directions,
and how to talk. It is considerably longer than the one the project started
with.

**Bought:** each of those took a paragraph rather than a feature. Widening was
one paragraph and worked first try.

**Cost:** behaviour that lives in prose is behaviour with no test. It can drift
when a model changes, and nothing fails when it does.

**Reverse:** delete any paragraph. The tools all work without them.

---

## 8. Accounts are keyed on the Google subject

**Now:** `AppUser.googleSubject` is unique and not updatable. Email is stored
for display only.

**Bought:** email addresses change and get reused. Keying on one means a new
owner of an old address inherits the previous owner's calendar history.

**Cost:** none found. This one is included because it is a schema decision -
painful to change once there are rows worth keeping - rather than because it
looks doubtful.

**Reverse:** a migration, not an edit. Decide now rather than later.

---

## 9. The Python service trusts a header

**Now:** `web.py` reads `X-User-Id` and believes it.

**Bought:** the calendar service stays free of authentication, which is the
point of putting the gateway in front of it.

**Cost:** anything able to reach the Python service directly can claim to be
anyone.

**Reverse:** either keep the service unreachable except from the gateway
(network), or have the gateway sign something the service verifies (shared
secret). **This one is not optional before anything is deployed** - it is only
safe while both processes are on your laptop.
