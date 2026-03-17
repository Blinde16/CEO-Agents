# Client Isolation Framework (n8n + Postgres + Redis + Agent)

This document is implementation-ready for a shared-infrastructure, multi-tenant SMB automation platform.

## 1) System Architecture Diagram (Text)

```text
                             ┌────────────────────────────────────┐
Inbound events (email/web)   │ API Gateway / Reverse Proxy       │
Scheduler / UI actions ─────▶│ - TLS termination                 │
                             │ - Path-based routing              │
                             │ - Rate limits by client_id        │
                             └──────────────┬─────────────────────┘
                                            │
                                            ▼
                             ┌────────────────────────────────────┐
                             │ Agent Service (Python/Node)       │
                             │ - AuthN + client_id validation    │
                             │ - Action allowlist per client     │
                             │ - HMAC-sign n8n webhook calls     │
                             │ - Structured audit logging        │
                             └───────┬─────────────┬──────────────┘
                                     │             │
                                     │             ▼
                                     │   ┌─────────────────────────┐
                                     │   │ Postgres                │
                                     │   │ - Multi-tenant tables   │
                                     │   │ - RLS policies          │
                                     │   │ - Audit trail           │
                                     │   └─────────────────────────┘
                                     │
                                     ▼
                        ┌──────────────────────────────────────────┐
                        │ n8n (single shared instance)            │
                        │ - Workflow naming: [CLIENT]__[NAME]     │
                        │ - Required input: client_id             │
                        │ - Credential per client/provider        │
                        │ - Guard node validates client context   │
                        └─────────────────┬────────────────────────┘
                                          │
                              Queue mode  ▼
                                  ┌───────────────────────────────┐
                                  │ Redis                         │
                                  │ - Per-client queues           │
                                  │ - Per-client concurrency caps │
                                  └───────────────────────────────┘
```

---

## 2) Step-by-Step Implementation Plan (Ordered)

1. **Create client identity primitives**
   - Add `clients` table and generate immutable `client_id` (`clt_<12 base32 chars>`).
   - Add per-client secret (`webhook_signing_secret`) for webhook signature validation.
   - Register allowed inbound domains/IPs for each client integration.

2. **Enforce identity in agent API contract**
   - Reject any request missing `client_id`, `request_id`, or `action`.
   - Resolve caller auth token → allowed `client_id` set.
   - Hard-fail if caller tries mismatched tenant.

3. **Enforce identity in n8n webhook ingress**
   - Webhook pattern: `/webhook/v1/c/{client_id}/{workflow_key}`.
   - Require headers `X-Client-Id`, `X-Signature`, `X-Timestamp`, `X-Request-Id`.
   - Validate `client_id` in path == header == payload.
   - Verify HMAC signature (`sha256`) with client secret.
   - Reject stale timestamps (>5 min).

4. **Implement tenant-safe Postgres schema**
   - Ensure every tenant data table has `client_id UUID NOT NULL` with FK to `clients(id)`.
   - Add composite indexes with `client_id` leading.
   - Add unique constraints scoped by `client_id` where needed.

5. **Enable Row Level Security (recommended now)**
   - Enable RLS on all tenant tables.
   - On each DB connection, run `SET app.client_id = '<uuid>'`.
   - Policy pattern: `USING (client_id = current_setting('app.client_id')::uuid)`.

6. **Apply execution isolation in n8n**
   - Naming standard: `[CLIENT_ID]__[DOMAIN]__[ACTION]`.
   - Add workflow static data/tag: `client_id`, `domain`, `tier`.
   - First node in every workflow: guard node asserting valid `client_id`.
   - Set per-client concurrency + retries in queue workers.

7. **Implement queue isolation (Redis mode)**
   - One queue key namespace per client: `n8n:q:client:{client_id}`.
   - Worker pool supports weighted fair scheduling by client tier.
   - Dead-letter queue per client: `n8n:dlq:client:{client_id}`.

8. **Implement integration/credential isolation**
   - One credential object per client/provider pair.
   - Credential naming: `client_{client_slug}_{provider}_{env}`.
   - Prohibit shared credentials by policy + nightly scanner.

9. **Add observability and audit filters**
   - Structured logs include `client_id`, `workflow_id`, `execution_id`, `request_id`.
   - Dashboard filters by exact `client_id` only.
   - Log redaction policy for tokens and message content.

10. **Run isolation verification suite before go-live**
   - Negative tests: missing client_id, spoofed headers, cross-client SQL queries.
   - Credential ownership checks.
   - Load test with one noisy client; verify fairness and unaffected latency for others.

---

## 3) Identity Isolation

### Design Principles
- Tenant identity is explicit, immutable, and mandatory in every request.
- Identity must be validated at each hop (gateway → agent → n8n → DB).
- Never infer tenant from mutable fields (email, workflow name alone).

### `client_id` Schema
- **Storage type**: UUID in Postgres (`clients.id`).
- **External display format**: `clt_<base32_12>` for URLs and logs.
- **Mapping**: `clients.external_id UNIQUE` maps to UUID.

Examples:
- `clt_7K2M4P9Q1T8Z`
- `clt_A1B2C3D4E5F6`

### Webhook URL Structure
- n8n inbound route:
  - `POST /webhook/v1/c/{client_external_id}/{workflow_key}`
- Examples:
  - `/webhook/v1/c/clt_7K2M4P9Q1T8Z/email_triage`
  - `/webhook/v1/c/clt_A1B2C3D4E5F6/calendar_reschedule`

### Validation Middleware (Pseudo-code)

```pseudo
function validateInbound(req):
    requiredHeaders = ["x-client-id", "x-signature", "x-timestamp", "x-request-id"]
    assertHeadersPresent(req, requiredHeaders)

    pathClient = req.pathParams.client_id
    headerClient = req.headers["x-client-id"]
    bodyClient = req.json.client_id

    if not (pathClient == headerClient == bodyClient):
        return 400 "client_id_mismatch"

    client = db.clients.findByExternalId(pathClient)
    if client is null or client.status != 'active':
        return 403 "unknown_or_inactive_client"

    if abs(now() - parseTimestamp(req.headers["x-timestamp"])) > 300s:
        return 401 "stale_request"

    computedSig = hmac_sha256(client.webhook_signing_secret,
                              req.headers["x-timestamp"] + "." + req.rawBody)
    if !constantTimeEqual(computedSig, req.headers["x-signature"]):
        return 401 "invalid_signature"

    req.context.client_uuid = client.id
    req.context.client_external_id = client.external_id
    return next()
```

Failure prevention:
- Reject missing/empty `client_id` (400).
- Reject client mismatch between path/header/body (400).
- Reject unsigned or stale requests (401).
- Reject unknown client or unauthorized caller (403).

---

## 4) Data Isolation

Use SQL in `docs/client_isolation_schema.sql`.

### Safe Query Patterns

```sql
-- Always tenant-scoped reads
SELECT id, title, status
FROM tasks
WHERE client_id = $1
  AND status IN ('open', 'in_progress')
ORDER BY created_at DESC
LIMIT 100;

-- Tenant-scoped update with optimistic lock
UPDATE events
SET status = 'rescheduled', updated_at = NOW(), version = version + 1
WHERE client_id = $1
  AND id = $2
  AND version = $3;

-- Workflow run fetch with tenant boundary
SELECT *
FROM workflow_runs
WHERE client_id = $1
  AND workflow_key = $2
  AND started_at >= NOW() - INTERVAL '7 days';
```

### Unsafe Query Example (Do Not Use)

```sql
SELECT * FROM email_logs WHERE message_id = $1;
```

Why unsafe: `message_id` may collide or be guessed; without `client_id` predicate this can expose another tenant’s row.

Correct form:

```sql
SELECT *
FROM email_logs
WHERE client_id = $1
  AND message_id = $2;
```

---

## 5) Execution Isolation

### Naming Standard
- Workflow name: `[CLIENT_ID]__[WORKFLOW_NAME]`
  - Example: `clt_7K2M4P9Q1T8Z__email_triage`
- Recommended extended format: `[CLIENT_ID]__[DOMAIN]__[ACTION]`
  - Example: `clt_7K2M4P9Q1T8Z__calendar__reschedule`

### Execution Control Strategy
- Every workflow starts with **Tenant Guard Node**:
  - Verify payload `client_id` exists.
  - Verify matches workflow metadata tag `client_id`.
  - Verify action belongs to client allowlist.
- Per-client limits:
  - `max_concurrency_per_client` (e.g., 5)
  - `max_retries_per_client_per_workflow` (e.g., 3)
  - `rate_limit_per_minute_per_client` (e.g., 120)
- Backpressure:
  - If client exceeds budget, return `429 tenant_rate_limited` and enqueue deferred.

### Queue Design (Redis)
- Namespaces:
  - `n8n:q:client:{client_id}:default`
  - `n8n:q:client:{client_id}:priority`
  - `n8n:dlq:client:{client_id}`
- Scheduler policy:
  - Weighted round-robin across client queues.
  - Hard cap per queue worker to prevent noisy-neighbor starvation.

---

## 6) Integration Isolation

### Credential Naming Standard
- `client_{client_external_id}_{provider}_{purpose}_{env}`
- Examples:
  - `client_clt_7K2M4P9Q1T8Z_google_oauth_mail_prod`
  - `client_clt_A1B2C3D4E5F6_microsoft_graph_calendar_prod`
  - `client_clt_A1B2C3D4E5F6_sendgrid_email_prod`

### Access Control Rules
- One credential set per `client_id + provider + purpose`.
- n8n workflow may reference only credentials tagged with same `client_id`.
- Platform admin actions for credentials require dual-control approval.
- Nightly policy job:
  - Detect credential objects referenced by multiple client IDs.
  - Disable offending workflows and raise critical alert.

### Secret Storage Strategy
- **Now (single VM)**: n8n encrypted credentials + Postgres KMS-style envelope key env var.
- **Next hardening**: external vault (HashiCorp Vault / cloud secret manager), short-lived tokens.
- Never store OAuth refresh tokens in plain env vars.

### Rotation Strategy
- Rotate API keys quarterly or on incident.
- OAuth re-consent every 90 days where provider permits.
- Keep dual active credentials during cutover:
  1. Create new credential.
  2. Update workflow references.
  3. Validate smoke tests by client.
  4. Revoke old credential.

---

## 7) Agent Layer Enforcement

### Agent Request Schema

```json
{
  "request_id": "req_01JABCDEF123",
  "client_id": "clt_7K2M4P9Q1T8Z",
  "actor": {
    "type": "user|system",
    "id": "usr_123",
    "ip": "203.0.113.5"
  },
  "action": "email.triage|calendar.reschedule|task.create",
  "payload": {},
  "idempotency_key": "idem_...",
  "timestamp": "2026-01-16T13:15:12Z"
}
```

### Validation Flow
1. Validate schema and required fields.
2. Authenticate caller token.
3. Resolve caller’s allowed `client_id` set.
4. Check `action` in client’s allowlist/capability map.
5. Attach `client_uuid` and `policy_version` to context.
6. Emit audit log entry.
7. Call n8n webhook with signed headers.

### Rejection Cases
- `400 missing_client_id`
- `400 invalid_client_id_format`
- `403 caller_not_allowed_for_client`
- `403 action_not_enabled_for_client`
- `409 duplicate_idempotency_key`
- `429 tenant_rate_limited`

---

## 8) Observability + Audit

### Logging Schema
- Required fields in every log line/event:
  - `timestamp`
  - `level`
  - `service` (`gateway|agent|n8n|worker|db`)
  - `client_id`
  - `client_uuid`
  - `request_id`
  - `workflow_id`
  - `execution_id`
  - `action`
  - `status`
  - `error_code` (nullable)

### Example Log Entries

```json
{"timestamp":"2026-01-16T13:15:12.981Z","level":"INFO","service":"agent","client_id":"clt_7K2M4P9Q1T8Z","request_id":"req_01JABC","action":"email.triage","workflow_id":"wf_123","status":"accepted"}
{"timestamp":"2026-01-16T13:15:13.442Z","level":"WARN","service":"n8n","client_id":"clt_7K2M4P9Q1T8Z","request_id":"req_01JABC","execution_id":"exec_7788","status":"rejected","error_code":"tenant_guard_failed"}
```

### Debugging Workflow
1. Start with `request_id`.
2. Filter logs by `client_id + request_id`.
3. Confirm gateway validation result.
4. Confirm agent policy decision and signed webhook.
5. Confirm n8n tenant guard node pass/fail.
6. Confirm DB queries include `client_id` and RLS context.

---

## 9) Failure Mode Checklist (Top 10)

1. **Missing `client_id` in request**
   - Risk: default/global context may be used accidentally.
   - Prevention: strict schema validation; reject at gateway and agent.

2. **Mismatched client across path/header/body**
   - Risk: tenant confusion, spoof attempt.
   - Prevention: equality check across all locations; fail closed.

3. **Unsigned or weakly signed webhook**
   - Risk: forged workflow execution.
   - Prevention: HMAC-SHA256 with per-client secret + timestamp window.

4. **Shared credentials between clients**
   - Risk: API operations executed in wrong external account.
   - Prevention: unique credential naming + policy scanner + deny shared references.

5. **SQL query missing `client_id` predicate**
   - Risk: cross-tenant read/write leakage.
   - Prevention: repository helper requiring tenant param + SQL lint rule + RLS.

6. **RLS disabled or bypassed by superuser connection**
   - Risk: entire tenant boundary collapses.
   - Prevention: app connects with non-superuser role; CI test asserts RLS enabled.

7. **Workflow cloned without updating client metadata**
   - Risk: wrong tenant processes incoming data.
   - Prevention: deployment script validates workflow name/tag/client match.

8. **Noisy neighbor saturation (one tenant floods queue)**
   - Risk: SLA degradation for other clients.
   - Prevention: per-client rate limits, concurrency caps, weighted scheduling.

9. **Log lines without `client_id`**
   - Risk: incident triage impossible; hidden cross-tenant events.
   - Prevention: logger wrapper requiring `client_id`; drop/flag nonconforming logs.

10. **Idempotency not tenant-scoped**
    - Risk: one tenant can block/replay another tenant request IDs.
    - Prevention: unique index on `(client_id, idempotency_key)`.

---

## 10) Naming Conventions Cheat Sheet

- Client external ID: `clt_<BASE32_12>`
- n8n workflow: `{client_id}__{domain}__{action}`
- Webhook route: `/webhook/v1/c/{client_id}/{workflow_key}`
- Credential: `client_{client_id}_{provider}_{purpose}_{env}`
- Redis queue: `n8n:q:client:{client_id}:{priority}`
- Dead-letter queue: `n8n:dlq:client:{client_id}`
- Request ID: `req_<ULID>`
- Idempotency key: `idem_<ULID>`
