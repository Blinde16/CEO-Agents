-- Multi-tenant isolation schema for n8n automation platform
-- Requires pgcrypto extension for gen_random_uuid()

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id TEXT NOT NULL UNIQUE CHECK (external_id ~ '^clt_[A-Z2-7]{12}$'),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'disabled')),
    webhook_signing_secret TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    external_ref TEXT,
    title TEXT NOT NULL,
    description TEXT,
    due_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
    priority SMALLINT NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, external_ref)
);

CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    calendar_provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'rescheduled', 'cancelled')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, calendar_provider, provider_event_id)
);

CREATE TABLE email_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    subject TEXT,
    from_address TEXT,
    to_addresses JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('received', 'triaged', 'drafted', 'sent', 'failed')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, provider, message_id)
);

CREATE TABLE workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
    workflow_id TEXT NOT NULL,
    workflow_key TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    UNIQUE (client_id, execution_id),
    UNIQUE (client_id, request_id),
    UNIQUE (client_id, idempotency_key)
);

-- Composite indexes with client_id first
CREATE INDEX idx_tasks_client_status_due ON tasks (client_id, status, due_at DESC);
CREATE INDEX idx_events_client_start ON events (client_id, starts_at DESC);
CREATE INDEX idx_email_logs_client_created ON email_logs (client_id, created_at DESC);
CREATE INDEX idx_workflow_runs_client_started ON workflow_runs (client_id, started_at DESC);
CREATE INDEX idx_workflow_runs_client_workflow_status ON workflow_runs (client_id, workflow_key, status);

-- Optional but recommended: Row-Level Security
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_tasks ON tasks
    USING (client_id = current_setting('app.client_id')::uuid)
    WITH CHECK (client_id = current_setting('app.client_id')::uuid);

CREATE POLICY tenant_isolation_events ON events
    USING (client_id = current_setting('app.client_id')::uuid)
    WITH CHECK (client_id = current_setting('app.client_id')::uuid);

CREATE POLICY tenant_isolation_email_logs ON email_logs
    USING (client_id = current_setting('app.client_id')::uuid)
    WITH CHECK (client_id = current_setting('app.client_id')::uuid);

CREATE POLICY tenant_isolation_workflow_runs ON workflow_runs
    USING (client_id = current_setting('app.client_id')::uuid)
    WITH CHECK (client_id = current_setting('app.client_id')::uuid);
