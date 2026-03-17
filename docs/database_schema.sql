CREATE TABLE clients (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  timezone TEXT NOT NULL,
  subscription_plan TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE users (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  role TEXT NOT NULL CHECK (role IN ('executive_user', 'platform_admin', 'operator')),
  email TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE client_configs (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id) UNIQUE,
  working_hours JSONB NOT NULL,
  scheduling_preferences JSONB NOT NULL,
  approval_rules JSONB NOT NULL,
  priority_contacts JSONB NOT NULL,
  email_tone_profile JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE actions (
  action_id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  user_id UUID NOT NULL REFERENCES users(id),
  action_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE approval_queue (
  approval_id UUID PRIMARY KEY,
  action_id UUID NOT NULL REFERENCES actions(action_id),
  client_id UUID NOT NULL REFERENCES clients(id),
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
  reviewer_id UUID,
  decision_time TIMESTAMPTZ
);

CREATE TABLE action_logs (
  id BIGSERIAL PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  user_id UUID NOT NULL REFERENCES users(id),
  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  action_type TEXT NOT NULL,
  action_status TEXT NOT NULL,
  error_message TEXT,
  executed_by TEXT NOT NULL,
  approval_status TEXT NOT NULL
);

CREATE TABLE integrations (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  provider TEXT NOT NULL,
  encrypted_credentials TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  source_action_id UUID REFERENCES actions(action_id),
  title TEXT NOT NULL,
  due_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE reminders (
  id UUID PRIMARY KEY,
  client_id UUID NOT NULL REFERENCES clients(id),
  source_action_id UUID REFERENCES actions(action_id),
  remind_at TIMESTAMPTZ NOT NULL,
  message TEXT NOT NULL,
  delivered BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_actions_client_time ON actions(client_id, created_at DESC);
CREATE INDEX idx_logs_client_time ON action_logs(client_id, timestamp DESC);
CREATE INDEX idx_approval_status ON approval_queue(status);
