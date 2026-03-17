# CEO-Agents v1.0

Initial production-oriented scaffold for a multi-tenant executive assistant platform.

## Included deliverables
- System architecture implementation (FastAPI service skeleton + deterministic action engine)
- PostgreSQL-oriented database schema
- OpenAPI specification
- Integration module stubs for calendar, email, task, and voice systems
- Admin/dashboard data contract notes
- Approval workflow primitives

## Quick start
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Run tests:
```bash
cd backend
pytest -q
```

## Notes
This repository provides a production-ready foundation with deterministic control flow and approval safety checks. External provider SDK wiring and deployment infrastructure can be layered on top of this baseline.
