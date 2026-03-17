# System Architecture (v1.0)

## Layers
1. **Interface Layer (Next.js + Tailwind)**
   - Executive dashboard
   - Onboarding form
   - Approval queue
   - Action log viewer
2. **API & Control Layer (FastAPI)**
   - Auth and tenant scoping
   - Intent parsing endpoint
   - Deterministic action engine
   - Approval gating and audit logging
3. **Execution Layer (n8n + Redis workers)**
   - Connector workflows
   - Scheduled jobs (daily briefing)
   - Retry policy handling
4. **Data Layer (PostgreSQL)**
   - Multi-tenant configs
   - Actions, approvals, reminders, tasks
   - Integration records and encrypted tokens

## Deterministic Control
- AI parsing is isolated to intent extraction.
- External API calls only occur through the action engine.
- Each action includes tenant context and approval state.

## Risk and Approval Policy
- `draft_email_reply`, `cancel_event`: always approval required
- `reschedule_event`: approval required for medium/high risk or priority contacts
- All approvals recorded with reviewer and decision timestamp

## Reliability and Observability
- Background queue for retries (Redis)
- Action log entity for immutable execution trail
- Health endpoint and API metrics integration point

## Voice Pipeline
1. Twilio captures call audio.
2. Speech-to-text transcript posted to `/intent/parse`.
3. Parsed intent transformed into action request.
4. Action routed through approval engine and logged.
