.PHONY: dev backend frontend install install-backend install-frontend \
        db-migrate docker-up docker-down

# ── Local dev (SQLite, no Docker) ─────────────────────────────────────────────

# One-command startup: backend + frontend in parallel
dev: install
	@echo "Starting CEO-Agents (local dev)..."
	@trap 'kill 0' SIGINT; \
	  (cd backend && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --reload --port 8000) & \
	  (cd frontend && npm run dev) & \
	  wait

backend:
	cd backend && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

install: install-backend install-frontend

install-backend:
	@if [ ! -d backend/.venv ]; then python3 -m venv backend/.venv; fi
	backend/.venv/bin/pip install -q -r backend/requirements.txt

install-frontend:
	cd frontend && npm install --silent

# Run Alembic migrations against DATABASE_URL (or SQLite default)
db-migrate:
	cd backend && .venv/bin/alembic upgrade head

# ── Docker Compose (Postgres + n8n + backend + frontend) ──────────────────────

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-reset:
	docker compose down -v
