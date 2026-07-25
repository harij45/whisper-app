# Whisper

Whisper is an anonymous shared-room support app. Rooms and messages are stored on
the server, so people on different devices can see and join the same conversations.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. Local development uses `data/whisper.db`.

## Deploy on Render

The included `render.yaml` creates:

- a Python web service;
- a Render Postgres database;
- the `DATABASE_URL` connection between them.

In Render, choose **New → Blueprint**, connect this repository, and deploy it.

If the web service already exists, create a Render Postgres database and add its
internal connection string to the web service as the `DATABASE_URL` environment
variable. Use these service settings:

```text
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
Health check: /health
```

Without `DATABASE_URL`, the app falls back to SQLite. That works locally and lets
different users share rooms while the service is running, but Render's free web
service filesystem is temporary, so SQLite rooms disappear after a restart or
spin-down. Postgres keeps them across web-service restarts.
