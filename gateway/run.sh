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

echo "Gateway starting on http://localhost:8080  (sign in at /me)"

# Maven finds its project by the working directory, not by where the wrapper
# lives, so this has to run from the gateway directory whatever the caller's.
cd "$here"
exec ./mvnw -q -B spring-boot:run
