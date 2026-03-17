# Google Setup

In this architecture, Google is connected to the backend, not to n8n.

`n8n` only calls backend webhook endpoints. The backend is responsible for:

- Gmail access
- Calendar access
- Contacts lookup
- OAuth token refresh

## Required backend env vars

Set these in `backend/.env`:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/integrations/google/callback
```

## Google Cloud Console

Create an OAuth client in Google Cloud with these APIs enabled:

- Gmail API
- Google Calendar API
- People API

Authorized redirect URI:

```text
http://localhost:8000/integrations/google/callback
```

## Connect a demo client

1. Create or verify a client exists:

```text
POST /clients
```

2. Start OAuth:

```text
GET /integrations/google/start?client_id=<client_id>
```

3. Open the returned `auth_url` in a browser.

4. Complete Google consent.

5. After redirect, verify:

```text
GET /integrations?client_id=<client_id>
```

You should see a `google` integration with `status=connected`.

## What n8n needs

Nothing Google-specific unless you want n8n itself to send Slack or Gmail messages.

For the core demo flows, n8n only needs:

- backend URL
- `X-N8N-Secret` header

## Fast verification

Once Google is connected for one client:

- `GET /briefing/next?client_id=<client_id>` should return a meeting brief
- `POST /webhooks/n8n/morning-briefing` should return at least one item if that client has an upcoming event
- `POST /webhooks/n8n/inbox-triage` should return results if that client has inbox messages
