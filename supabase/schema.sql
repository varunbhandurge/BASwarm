-- ==============================================================================
-- BA ACCOUNT SWARM — SUPABASE POSTGRES SCHEMA MIGRATION
-- ==============================================================================
-- Database schema for multi-agent swarm state, Human-in-the-Loop (HITL) approvals,
-- requirement traceability mapping, and execution audit logging.

-- 1. Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. Account Context & Traceability Table
-- Stores persistent knowledge per client account, linking CRM deals to Jira projects
-- and maintaining the cross-system requirement-to-ticket traceability matrix.
CREATE TABLE IF NOT EXISTS account_context (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_name TEXT NOT NULL UNIQUE,
    zoho_deal_id TEXT,
    jira_project_key TEXT,
    whatsapp_phone TEXT,                            -- WhatsApp direct sender ID or phone number
    req_traceability_map JSONB DEFAULT '{}'::jsonb, -- Maps REQ-001 -> Jira Issue Keys (e.g., {"REQ-001": ["PROJ-101", "PROJ-102"]})
    metadata JSONB DEFAULT '{}'::jsonb,             -- SLA tiers, contacts, active commercial terms
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_account_context_account_name ON account_context(account_name);
CREATE INDEX IF NOT EXISTS idx_account_context_jira_key ON account_context(jira_project_key);
CREATE INDEX IF NOT EXISTS idx_account_context_zoho_deal ON account_context(zoho_deal_id);
CREATE INDEX IF NOT EXISTS idx_account_context_whatsapp_phone ON account_context(whatsapp_phone);

-- 3. Human-in-the-Loop (HITL) Approvals Table
-- Intercepts high-risk external actions (Zoho CRM field updates, email sending, WhatsApp alerts, SLA commitments)
-- and stages them for human review before execution.
CREATE TABLE IF NOT EXISTS hitl_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name TEXT NOT NULL,                                  -- e.g., 'commercial', 'comms', 'delivery'
    action_type TEXT NOT NULL,                                 -- e.g., 'zoho_update_deal', 'email_send_approved', 'whatsapp_send_message', 'jira_transition_issue'
    target_system TEXT NOT NULL,                               -- e.g., 'zoho', 'gmail', 'outlook', 'whatsapp', 'jira'
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,                -- Serialized payload containing parameters for the tool call
    risk_summary TEXT NOT NULL,                                -- Human-readable justification of risk (e.g., "Sends external message with revised pricing")
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    rejection_reason TEXT,
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_hitl_approvals_status ON hitl_approvals(status);
CREATE INDEX IF NOT EXISTS idx_hitl_approvals_agent ON hitl_approvals(agent_name);
CREATE INDEX IF NOT EXISTS idx_hitl_approvals_created_at ON hitl_approvals(created_at DESC);

-- 4. Swarm Audit Logs Table
-- Immutable ledger documenting every incoming event, multi-agent handoff path,
-- discrepancy alerts detected, and synthesized final summaries.
CREATE TABLE IF NOT EXISTS swarm_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_source TEXT NOT NULL,                                -- e.g., 'gmail', 'outlook', 'whatsapp', 'voice', 'meeting_transcript', 'deployment_note', 'daily_cron'
    initial_event TEXT NOT NULL,                               -- Raw or truncated prompt/event payload received
    agent_handoff_path JSONB NOT NULL DEFAULT '[]'::jsonb,     -- Array of hops: [{"from": "triage", "to": "commercial", "reason": "..."}, ...]
    discrepancies_detected JSONB NOT NULL DEFAULT '[]'::jsonb, -- Detected deviations against existing Zoho terms or Jira scope
    final_summary TEXT NOT NULL,                               -- Synthesized outcome, artifacts generated, or queued actions
    execution_time_ms INTEGER DEFAULT 0,                       -- Latency of full multi-agent turn execution
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_swarm_audit_logs_source ON swarm_audit_logs(event_source);
CREATE INDEX IF NOT EXISTS idx_swarm_audit_logs_created_at ON swarm_audit_logs(created_at DESC);

-- 5. Rate Cards Table (Commercial Rate-Card & Effort Estimator)
-- Baseline hourly billable rates for scoping, milestone calculations, and discount margin alerts.
CREATE TABLE IF NOT EXISTS rate_cards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_name TEXT NOT NULL UNIQUE,                             -- e.g. 'Lead BA / Technical PM', 'Senior Full-Stack Engineer', 'QA & DevOps Specialist'
    rate_per_hour NUMERIC(10, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

-- Seed default commercial rate-card data
INSERT INTO rate_cards (role_name, rate_per_hour, currency) VALUES
    ('Lead BA / Technical PM', 120.00, 'USD'),
    ('Senior Full-Stack Engineer', 100.00, 'USD'),
    ('QA & DevOps Specialist', 85.00, 'USD')
ON CONFLICT (role_name) DO UPDATE
    SET rate_per_hour = EXCLUDED.rate_per_hour,
        updated_at = timezone('utc', now());

-- 6. RAID Logs Table (Risks, Assumptions, Issues, Dependencies)
-- Live tracking of project risks, scope impediments, and architectural dependencies.
CREATE TABLE IF NOT EXISTS raid_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Risk', 'Assumption', 'Issue', 'Dependency')),
    severity TEXT NOT NULL CHECK (severity IN ('High', 'Medium', 'Low')),
    description TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'Unassigned',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'mitigated', 'resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_raid_logs_account ON raid_logs(account_name);
CREATE INDEX IF NOT EXISTS idx_raid_logs_status ON raid_logs(status);
CREATE INDEX IF NOT EXISTS idx_raid_logs_category ON raid_logs(category);

-- 7. Auto-update Trigger for updated_at timestamps
CREATE OR REPLACE FUNCTION update_timestamp_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc', now());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_account_context_timestamp ON account_context;
CREATE TRIGGER trg_update_account_context_timestamp
    BEFORE UPDATE ON account_context
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp_column();

DROP TRIGGER IF EXISTS trg_update_hitl_approvals_timestamp ON hitl_approvals;
CREATE TRIGGER trg_update_hitl_approvals_timestamp
    BEFORE UPDATE ON hitl_approvals
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp_column();

DROP TRIGGER IF EXISTS trg_update_rate_cards_timestamp ON rate_cards;
CREATE TRIGGER trg_update_rate_cards_timestamp
    BEFORE UPDATE ON rate_cards
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp_column();

DROP TRIGGER IF EXISTS trg_update_raid_logs_timestamp ON raid_logs;
CREATE TRIGGER trg_update_raid_logs_timestamp
    BEFORE UPDATE ON raid_logs
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp_column();

-- 8. Row Level Security (RLS) Configuration
ALTER TABLE account_context ENABLE ROW LEVEL SECURITY;
ALTER TABLE hitl_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE swarm_audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE raid_logs ENABLE ROW LEVEL SECURITY;

-- Allow full access to service_role (used by backend with SUPABASE_SERVICE_KEY)
DROP POLICY IF EXISTS service_role_all_account_context ON account_context;
CREATE POLICY service_role_all_account_context ON account_context
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS service_role_all_hitl_approvals ON hitl_approvals;
CREATE POLICY service_role_all_hitl_approvals ON hitl_approvals
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS service_role_all_swarm_audit_logs ON swarm_audit_logs;
CREATE POLICY service_role_all_swarm_audit_logs ON swarm_audit_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS service_role_all_rate_cards ON rate_cards;
CREATE POLICY service_role_all_rate_cards ON rate_cards
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS service_role_all_raid_logs ON raid_logs;
CREATE POLICY service_role_all_raid_logs ON raid_logs
    FOR ALL TO service_role USING (true) WITH CHECK (true);
