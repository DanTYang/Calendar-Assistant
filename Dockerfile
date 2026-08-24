# The calendar service.
#
# Runs under gunicorn rather than Flask's development server, which says on
# every startup that it is not for production and means it.
FROM python:3.12-slim

# Bytecode files and buffered output are both wrong in a container: the first
# is written to a filesystem that will not persist, and the second means logs
# arrive late or not at all when a process is killed.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, as their own layer. They change far less often than the
# code does, so this is the difference between a rebuild fetching packages
# every time and almost never.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py .
COPY assistant/ ./assistant/
COPY data/ ./data/

# Nothing here needs root, so nothing here gets it.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# ONE worker, and threads for concurrency. This is not a tuning choice.
#
# Conversations live in a dictionary inside the process. A second worker is a
# second process with a second dictionary, and requests are handed out between
# them - so the same person would get a different conversation depending on
# which worker answered, at random, mid-conversation.
#
# Raise this only after conversations move into shared storage. The same change
# is what would let the service run more than one instance.
#
# The timeout is generous because an answer is several model calls and a
# streamed one holds the connection for all of them. The default 30s would cut
# long answers off mid-sentence.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "16", "--worker-class", "gthread", \
     "--timeout", "180", "--access-logfile", "-", \
     "assistant.wsgi:app"]
