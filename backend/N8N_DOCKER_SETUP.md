# n8n Docker Setup

This repo now includes [docker-compose.yml](C:/Users/Blake/Documents/Projects/ceo-agents/backend/docker-compose.yml) for local `n8n` and `postgres`.

## Recommended local setup

- Run the FastAPI backend in your existing Python `.venv`.
- Run `n8n` and `postgres` in Docker.
- In n8n, call the backend at `http://host.docker.internal:8000`.

That is the fastest path for the demo because you do not need to rebuild the backend image every time you change Python code.

## 1. Start Docker services

From the backend folder:

```powershell
docker compose up -d
```

Check status:

```powershell
docker compose ps
```

Open n8n:

```text
http://localhost:5678
```

## 2. Run the backend in the Python venv

Use your existing `.env` and run:

```powershell
.\.venv\Scripts\Activate.ps1
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```text
http://localhost:8000/health
```

## 3. Configure backend env for Docker Postgres

In `.env`, set:

```env
DATABASE_URL=postgresql://ceo:ceopassword@localhost:5432/ceo_agents
POSTGRES_PASSWORD=ceopassword
N8N_WEBHOOK_SECRET=change-me-to-a-random-secret
N8N_ENCRYPTION_KEY=change-me-in-production
```

If you want to stay on SQLite for the demo, you can. Docker Postgres is optional for the backend. The backend does not need to run in Docker unless you want a fully containerized stack.

## 4. Create n8n credentials and test backend reachability

Inside n8n, create an `HTTP Request` node:

- Method: `POST`
- URL: `http://host.docker.internal:8000/webhooks/n8n/morning-briefing`
- Send Headers: `true`
- Header: `X-N8N-Secret`
- Value: your `N8N_WEBHOOK_SECRET`

Run it manually.

Expected:

- `200` with JSON containing `count`, `briefings`, `error_count`, `errors`

If it fails:

- `401`: secret mismatch
- connection refused: backend not running on port `8000`
- empty `briefings`: no connected Google client, no upcoming events, or no data

## 5. Minimal workflow layout

### Morning briefing

- `Manual Trigger`
- `HTTP Request` -> backend `/webhooks/n8n/morning-briefing`
- `IF` node -> `{{$json["count"] > 0}}`
- `Split Out` on `briefings`
- `Set` node to format message
- `Slack` or `Email` node

### Pre-meeting brief

- `Schedule Trigger` every 5 minutes
- `HTTP Request` -> backend `/webhooks/n8n/pre-meeting`
- `IF` node -> `{{$json["count"] > 0}}`
- dedupe by `client_id + event_id + start_time`
- send Slack/email

### Inbox triage

- `Manual Trigger` or hourly `Schedule Trigger`
- `HTTP Request` -> backend `/webhooks/n8n/inbox-triage`
- `IF` node -> `{{$json["client_count"] > 0}}`
- `Split Out` on `results`
- send summary

## 6. Do you run n8n in the same Python venv?

No.

- The FastAPI backend runs in your Python `.venv`.
- `n8n` runs as a separate Docker container.
- They communicate over HTTP.

You only use the Python venv for the backend, tests, Alembic, and local Python tooling.

## 7. Useful commands

Start stack:

```powershell
docker compose up -d
```

Stop stack:

```powershell
docker compose down
```

Stop and remove volumes:

```powershell
docker compose down -v
```

View n8n logs:

```powershell
docker compose logs -f n8n
```

View Postgres logs:

```powershell
docker compose logs -f postgres
```
