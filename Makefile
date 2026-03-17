.PHONY: dev backend frontend install install-backend install-frontend

# One-command startup: both backend and frontend in parallel
dev: install
	@echo "Starting CEO-Agents..."
	@trap 'kill 0' SIGINT; \
	  (cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) & \
	  (cd frontend && npm run dev) & \
	  wait

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

install: install-backend install-frontend

install-backend:
	@if [ ! -d backend/.venv ]; then python3 -m venv backend/.venv; fi
	backend/.venv/bin/pip install -q -r backend/requirements.txt

install-frontend:
	cd frontend && npm install --silent
