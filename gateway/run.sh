#!/usr/bin/env bash
# Start the gateway, taking the OAuth client from the JSON the console gave you
# rather than from variables typed by hand.
#
# The file is git-ignored and stays that way: this reads it at startup and puts
# the values in the environment of one process. Nothing is copied into a file
# that gets committed, and nothing is echoed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
client="${OAUTH_CLIENT_FILE:-$here/../OAuthCrediential.json}"

if [[ ! -f "$client" ]]; then
  echo "No OAuth client file at $client" >&2
  echo "Download one from the Google Cloud console (type: Web application)," >&2
  echo "or point OAUTH_CLIENT_FILE at it." >&2
  exit 1
fi

read -r id secret < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
web = d.get("web")
if not web:
    sys.exit(f"{sys.argv[1]} is a {next(iter(d))!r} client; Spring needs a \"web\" one")
print(web["client_id"], web["client_secret"])
' "$client")

export GOOGLE_CLIENT_ID="$id"
export GOOGLE_CLIENT_SECRET="$secret"

# The secret shared with the calendar service, read from the same .env that
# service reads. One source, so the two halves cannot drift apart - a mismatch
# would show up as every request being refused, and a value only set on one
# side would silently leave the service open.
env_file="${ENV_FILE:-$here/../.env}"
if [[ -z "${GATEWAY_SECRET:-}" && -f "$env_file" ]]; then
  GATEWAY_SECRET="$(python3 -c '
import pathlib, sys
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    key, _, value = line.partition("=")
    if key.strip() == "GATEWAY_SECRET":
        print(value.strip().strip("\"\x27"))
        break
' "$env_file")"
fi
export GATEWAY_SECRET="${GATEWAY_SECRET:-}"

if [[ -z "$GATEWAY_SECRET" ]]; then
  echo "warning: GATEWAY_SECRET is not set, so the calendar service will" >&2
  echo "         answer anyone who can reach its port. Fine on one machine." >&2
fi

echo "Gateway starting on http://localhost:8080  (sign in at /me)"

# Maven finds its project by the working directory, not by where the wrapper
# lives, so this has to run from the gateway directory whatever the caller's.
cd "$here"
exec ./mvnw -q -B spring-boot:run
