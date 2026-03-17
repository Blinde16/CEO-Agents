# n8n Demo Checklist

Use this to verify the backend, connectors, and workflow wiring before the partner meeting.

## Backend readiness

- Start the API and confirm `GET /health` returns `status=ok`.
- Confirm `.env` has valid `OPENAI_API_KEY`, Google OAuth values, `DATABASE_URL`, and `N8N_WEBHOOK_SECRET`.
- Confirm at least one demo client exists in `/clients`.
- Confirm that client has a connected Google integration in `/integrations?client_id=<client_id>`.
- Confirm the demo client has:
  - at least one upcoming calendar event
  - at least one inbox thread from a meeting attendee

## Connector verification in n8n

### HTTP connector to backend

- Create an `HTTP Request` node to `POST /webhooks/n8n/morning-briefing`.
- Add header `X-N8N-Secret` with the same value as `N8N_WEBHOOK_SECRET`.
- Run the node manually.
- Verify response contains:
  - `count`
  - `briefings`
  - `error_count`
  - `errors`
- Expect `401` if the secret is wrong. Fix this before testing anything else.

### Slack connector

- Add a manual test node that sends a fixed message to your demo DM/channel.
- Confirm the target channel is correct.
- Confirm n8n can post without additional OAuth prompts.
- Keep one simple “hello from n8n” node available as a fallback proof point.

### Gmail or Email delivery connector

- Send a fixed test email to yourself from n8n.
- Verify sender identity, subject formatting, and inbox delivery.
- If using Gmail in n8n, verify the account is the intended demo account, not a personal fallback.

## Workflow checks

### 1. Morning briefing

- Trigger: `Manual Trigger` for rehearsal, then `Schedule Trigger` for production.
- Flow:
  - `HTTP Request` -> backend `/webhooks/n8n/morning-briefing`
  - `IF` node: `count > 0`
  - `Split Out` or item iteration over `briefings`
  - format message
  - send to Slack or email
- Verify one returned briefing includes:
  - `client_name`
  - `event_title`
  - `start_time`
  - `relationship_context`
  - `open_items`
  - `suggested_talking_points`

### 2. Pre-meeting brief

- Trigger: `Manual Trigger` for rehearsal, `Schedule Trigger` every 5 minutes if you keep it live.
- Flow:
  - `HTTP Request` -> backend `/webhooks/n8n/pre-meeting`
  - `IF` node: `count > 0`
  - iterate `briefings`
  - send alert
- Add dedupe before sending:
  - key on `client_id + event_id + start_time`
  - store/send once per event
- If you do not have dedupe set up, do not demo this live on a schedule.

### 3. Inbox triage

- Trigger: `Manual Trigger` for rehearsal, `Schedule Trigger` hourly if needed.
- Flow:
  - `HTTP Request` -> backend `/webhooks/n8n/inbox-triage`
  - `IF` node: `client_count > 0`
  - iterate `results`
  - optionally filter where `urgent_count > 0` or `action_required_count > 0`
  - send summary to Slack or email
- Verify at least one item shows:
  - `subject`
  - `sender`
  - `urgency_score`
  - `summary`
  - `action_items`

## Failure handling

- Inspect `error_count` and `errors` from each webhook response.
- If one client is failing, continue the workflow for the others and log the failure instead of stopping the whole run.
- In n8n, set Slack/email nodes to continue or branch on failures during rehearsal so one delivery issue does not kill the demo.

## Recommended demo sequence

- Show backend health.
- Run `morning-briefing` manually in n8n.
- Show returned JSON briefly.
- Show the Slack or email delivery output.
- Optionally show `inbox-triage` manually.
- Skip live scheduled `pre-meeting` unless dedupe is configured and tested.
