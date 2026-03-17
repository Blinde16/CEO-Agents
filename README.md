# CEO-Agents v1.0

Minimal demo console for testing an approval-first executive assistant workflow focused on email and calendar.

## What is implemented
- FastAPI control plane for client onboarding, intent parsing, action submission, approval decisions, and audit logs
- Mocked email and calendar execution so the demo is stable without external provider credentials
- Next.js operator console for creating a client, submitting actions, approving them, and reviewing outcomes
- Test coverage for core approval behavior and action gating

## Run the demo

Backend:
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The frontend talks to `http://localhost:8000` by default.

If you want to point the frontend somewhere else:
```bash
set NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Run tests
```bash
cd backend
.venv\Scripts\activate
python -m pytest -q
```

## Add AI And Google Connectors
1. Copy [backend/.env.example](C:\Users\Blake\Documents\Projects\ceo-agents\backend\.env.example) to `backend/.env`.
2. Set `OPENAI_API_KEY` to enable LLM-driven drafting and follow-up questions.
3. Create a Google OAuth client and set:
   `GOOGLE_CLIENT_ID`
   `GOOGLE_CLIENT_SECRET`
   `GOOGLE_REDIRECT_URI=http://localhost:8000/integrations/google/callback`
4. Keep `APP_BASE_URL=http://localhost:3000` for local development.

With those values in place:
- The assistant will use OpenAI when available and fall back to the deterministic demo logic otherwise.
- The frontend `Connect Google` button will call `/integrations/google/start`, redirect through Google OAuth, and return to the app when connected.
- Google scopes currently requested are Gmail compose and Calendar event access.

## Demo scope
- `draft_email_reply` always requires approval
- `cancel_event` always requires approval
- `create_event` executes immediately
- `reschedule_event` requires approval when the parsed request is medium or high risk

## Notes
This is a controlled demo environment. State is stored in memory and can be cleared from the UI with `Reset Demo`. Voice flows and real provider integrations should be added only after this base loop is validated.
