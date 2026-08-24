"""The entry point gunicorn imports.

`web.main()` runs Flask's own server, which is for development. A production
server wants the application object instead and does its own listening, so this
builds one and stops.

The source is read from the environment because it is a deployment decision:
the same image serves a sample calendar or every caller's real one depending
on how it is started.
"""

import os

from assistant.web import create_app

app = create_app(os.environ.get("CALENDAR_SOURCE", "api"))
