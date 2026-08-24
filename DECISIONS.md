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

**Reverse:** remove the `prompt` query parameter from `SecurityConfig`. Keep
`access_type=offline`.

**The precondition is now met** - tokens are stored and reused, so a returning
user has a refresh token already. What is left is the failure mode if that row
is ever missing: someone who has approved before, whose stored token was lost
(database wiped, migrated, or a new deployment), signs in, gets no refresh
token because Google sees an existing grant, and breaks an hour later. Forcing
consent means that can never happen. Removing it trades a prompt for a failure
that is rare, delayed, and confusing. Worth doing once the storage is something
you trust; not worth it while the database is a file in `gateway/data`.

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

## 5. Location classification errs towards "somewhere to go"

**Now:** `queries.location_kind` returns `place` unless the location names a
video call or a meeting room.

**Bought:** Google Maps resolves "Javits Center" and "Newark EWR" as readily as
a postal address, so refusing anything that is not a full address would refuse
most real events.

**Cost:** an odd location gets an odd link rather than nothing.

**Reverse:** tighten `location_kind` to require something address-shaped. The
boundary is stated in `queries.VIRTUAL_MARKERS` and `queries.ROOM_PREFIX`:
anything they do not match is treated as a place.

---

## 6. Behaviour specified in prose, not code

**Now:** the system prompt in `agent.py` carries widening, offering directions,
and how to talk. It is considerably longer than the one the project started
with.

**Bought:** each of those took a paragraph rather than a feature. Widening was
one paragraph and worked first try.

**Cost:** behaviour that lives in prose is behaviour with no test. It can drift
when a model changes, and nothing fails when it does.

**Reverse:** delete any paragraph. The tools all work without them.

---

## 7. Accounts are keyed on the Google subject

**Now:** `AppUser.googleSubject` is unique and not updatable. Email is stored
for display only.

**Bought:** email addresses change and get reused. Keying on one means a new
owner of an old address inherits the previous owner's calendar history.

**Cost:** none found. This one is included because it is a schema decision -
painful to change once there are rows worth keeping - rather than because it
looks doubtful.

**Reverse:** a migration, not an edit. Decide now rather than later.

---

## 8. A shared secret, rather than a signed token

**Settled.** The open version of this - the Python service believing
`X-User-Id` from anyone - is closed. `GATEWAY_SECRET` is read from `.env` by
both halves, sent as `X-Gateway-Key`, and compared in constant time. Without
it, every endpoint but `/health` answers 401.

**What is still traded:** the secret is symmetric. It proves the caller is the
gateway and nothing else - not which user, and not that the request was not
replayed. Anyone who reads the secret can use it, and it is held in two places.

**The alternative** is the gateway signing a short-lived token naming the user,
which the calendar service verifies with a public key. Then the calendar
service holds no secret, the claim is about a user rather than a service, and
a leaked token expires.

**Reverse:** unset `GATEWAY_SECRET` and the check disables itself, which is
what keeps the terminal and single-user setups working.

**Worth revisiting when** the two services stop being on one machine.

---

## 9. Spending is tracked in files

**Now:** `data/spend/`, one JSON file per person, hashed filenames like the
saved facts.

**Bought:** no database in the calendar service, which is what keeps it
stateless and lets it be deployed without one.

**Cost:** a deploy resets everyone's daily spend, because an App Runner
container starts with an empty filesystem. And two instances would each count
separately, so the real total would be the sum of both - which is why the
service has to stay pinned to one.

**Reverse:** move the ledger into the gateway's Postgres, beside the accounts.
The awkward part is that it would make the calendar service depend on a
database it currently knows nothing about; the alternative is DynamoDB, which
it could reach without one.

**Worth doing when** the limit has to hold rather than mostly hold.

---

## 10. Deployed, the calendar service is reachable from the internet

**Now:** both App Runner services are public. `GATEWAY_SECRET` is the only
thing between the calendar service and anyone who finds its URL.

**Bought:** no VPC ingress endpoint to configure, and the deployment stays
short enough to follow.

**Cost:** the secret is now load-bearing in a way it is not on a laptop. It is
symmetric, it lives in two places, and anyone who reads it can use it.

**Reverse:** an App Runner VPC ingress endpoint makes the calendar service
private, and then the secret is a second layer rather than the only one.

**Worth doing when** this stops being a personal project. Related: entry 8.

---

## 11. All model output is streamed, not just the answer

**Now:** `ask_stream` yields text from every step. The model often says what it
is about to do before reaching for a tool, and that text reaches the page.

**Bought:** something appears sooner, and it is real output rather than a
placeholder.

**Cost:** the page shows preamble that the plain `/chat` endpoint would never
have returned - `ask` still returns only the final reply. The two endpoints can
therefore show slightly different text for the same question.

**Reverse:** buffer text per step and only yield it once the step turns out to
have no tool calls. The cost is that the buffered step is no longer streamed at
all, since whether it is the last one is only known when it finishes.
