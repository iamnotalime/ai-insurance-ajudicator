-- PostgreSQL initialization script for Insurance Adjudicator
-- This runs automatically when the PostgreSQL container starts for the first time

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search optimization

-- Create the database if it doesn't exist (handled by POSTGRES_DB env var)
-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE insurance_adjudicator TO postgres;

-- Create enum types (if not using Alembic migrations)
DO $$ BEGIN
    CREATE TYPE claimtype AS ENUM (
        'auto', 'home', 'health', 'life', 'disability',
        'liability', 'property', 'workers_compensation'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE claimstatus AS ENUM (
        'submitted', 'pending_documents', 'under_review', 'pending_investigation',
        'agent_processing', 'human_review_required', 'approved', 'partially_approved',
        'denied', 'appealed', 'closed', 'fraud_suspected'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE priority AS ENUM ('low', 'medium', 'high', 'urgent');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- ============================================================
-- Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS policy_holders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE NOT NULL,
    address JSONB DEFAULT '{}',
    risk_score FLOAT,
    previous_claims_count INTEGER DEFAULT 0,
    ssn_encrypted VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_policy_holders_email ON policy_holders(email);
CREATE INDEX IF NOT EXISTS ix_policy_holders_name ON policy_holders(last_name, first_name);

CREATE TABLE IF NOT EXISTS policies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    policy_number VARCHAR(50) UNIQUE NOT NULL,
    policy_type claimtype NOT NULL,
    holder_id UUID NOT NULL REFERENCES policy_holders(id),
    effective_date DATE NOT NULL,
    expiration_date DATE NOT NULL,
    premium NUMERIC(15, 2) NOT NULL,
    total_coverage_limit NUMERIC(15, 2) NOT NULL,
    aggregate_deductible NUMERIC(15, 2) NOT NULL,
    endorsements JSONB DEFAULT '[]',
    exclusions JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT TRUE,
    auto_renewal BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_policies_policy_number ON policies(policy_number);
CREATE INDEX IF NOT EXISTS ix_policies_effective_dates ON policies(effective_date, expiration_date);
CREATE INDEX IF NOT EXISTS ix_policies_type_active ON policies(policy_type, is_active);

CREATE TABLE IF NOT EXISTS coverage_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    policy_id UUID NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    coverage_type VARCHAR(100) NOT NULL,
    limit_amount NUMERIC(15, 2) NOT NULL,
    deductible NUMERIC(15, 2) NOT NULL,
    copay_percentage FLOAT,
    exclusions JSONB DEFAULT '[]',
    conditions JSONB DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS claims (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_number VARCHAR(50) UNIQUE NOT NULL,
    claim_type claimtype NOT NULL,
    status claimstatus NOT NULL DEFAULT 'submitted',
    priority priority NOT NULL DEFAULT 'medium',
    policy_id UUID NOT NULL REFERENCES policies(id),
    claimant_id UUID NOT NULL REFERENCES policy_holders(id),
    date_of_loss DATE NOT NULL,
    date_reported DATE NOT NULL,
    description TEXT NOT NULL,
    location_of_loss VARCHAR(500),
    total_amount_claimed NUMERIC(15, 2) NOT NULL,
    assigned_to VARCHAR(100),
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    agent_iterations INTEGER DEFAULT 0,
    agent_reasoning_chain JSONB DEFAULT '[]',
    workflow_errors JSONB DEFAULT '[]',
    failed_steps JSONB DEFAULT '[]',
    last_error_message TEXT,
    last_error_at TIMESTAMPTZ,
    amount_discrepancy NUMERIC(15, 2),
    amount_discrepancy_flagged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_claims_claim_number ON claims(claim_number);
CREATE INDEX IF NOT EXISTS ix_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS ix_claims_type_status ON claims(claim_type, status);
CREATE INDEX IF NOT EXISTS ix_claims_date_reported ON claims(date_reported);
CREATE INDEX IF NOT EXISTS ix_claims_policy ON claims(policy_id);

CREATE TABLE IF NOT EXISTS claim_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    category VARCHAR(100) NOT NULL,
    amount_claimed NUMERIC(15, 2) NOT NULL,
    amount_approved NUMERIC(15, 2),
    date_of_loss DATE NOT NULL,
    location VARCHAR(500),
    supporting_document_ids JSONB DEFAULT '[]',
    notes TEXT,
    coverage_applicable VARCHAR(100),
    deductible_applied NUMERIC(15, 2) DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    document_type VARCHAR(100) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    extracted_text TEXT,
    extraction_confidence FLOAT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    verified BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_documents_type ON documents(document_type);

CREATE TABLE IF NOT EXISTS adjudication_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id UUID NOT NULL UNIQUE REFERENCES claims(id) ON DELETE CASCADE,
    decision VARCHAR(50) NOT NULL,
    confidence_score FLOAT NOT NULL,
    approved_amount NUMERIC(15, 2) NOT NULL,
    denied_amount NUMERIC(15, 2) NOT NULL,
    reasons JSONB DEFAULT '[]',
    detailed_explanation TEXT NOT NULL,
    coverage_analysis JSONB DEFAULT '{}',
    policy_compliance BOOLEAN DEFAULT TRUE,
    requires_human_review BOOLEAN DEFAULT FALSE,
    human_review_reasons JSONB DEFAULT '[]',
    risk_assessment JSONB,
    decided_at TIMESTAMPTZ DEFAULT NOW(),
    decided_by VARCHAR(100) DEFAULT 'ai_adjudicator',
    appeal_eligible BOOLEAN DEFAULT TRUE,
    appeal_deadline DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fraud_indicators (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_id UUID NOT NULL REFERENCES adjudication_decisions(id) ON DELETE CASCADE,
    indicator_type VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    severity VARCHAR(20) NOT NULL,
    confidence FLOAT NOT NULL,
    evidence JSONB DEFAULT '[]',
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflow_executions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    current_step VARCHAR(100),
    steps_completed JSONB DEFAULT '[]',
    steps_failed JSONB DEFAULT '[]',
    pending_steps JSONB DEFAULT '[]',
    agent_results JSONB DEFAULT '{}',
    shared_context JSONB DEFAULT '{}',
    total_execution_time_ms FLOAT DEFAULT 0,
    requires_human_review BOOLEAN DEFAULT FALSE,
    human_review_reasons JSONB DEFAULT '[]',
    error_message TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_workflow_claim ON workflow_executions(claim_id);
CREATE INDEX IF NOT EXISTS ix_workflow_status ON workflow_executions(status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id UUID,
    actor_type VARCHAR(50) NOT NULL,
    actor_id VARCHAR(100),
    actor_name VARCHAR(200),
    ip_address VARCHAR(45),
    user_agent VARCHAR(500),
    old_value JSONB,
    new_value JSONB,
    correlation_id UUID,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS ix_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_logs(actor_type, actor_id);
CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_correlation ON audit_logs(correlation_id);

-- ============================================================
-- Auto-update trigger for updated_at columns
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_policy_holders_updated_at BEFORE UPDATE ON policy_holders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_policies_updated_at BEFORE UPDATE ON policies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_claims_updated_at BEFORE UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_adjudication_decisions_updated_at BEFORE UPDATE ON adjudication_decisions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_workflow_executions_updated_at BEFORE UPDATE ON workflow_executions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- Seed data: Sample policy holder and policy for development
-- ============================================================

INSERT INTO policy_holders (id, first_name, last_name, email, date_of_birth, address, risk_score, previous_claims_count)
VALUES (
    'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
    'John', 'Doe', 'john.doe@example.com',
    '1985-05-15',
    '{"street": "123 Main St", "city": "Anytown", "state": "CA", "zip": "90210"}',
    0.3, 1
) ON CONFLICT DO NOTHING;

INSERT INTO policies (id, policy_number, policy_type, holder_id, effective_date, expiration_date, premium, total_coverage_limit, aggregate_deductible, exclusions)
VALUES (
    'b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a22',
    'POL-2024-001', 'auto',
    'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11',
    '2024-01-01', '2025-12-31',
    1200.00, 200000.00, 500.00,
    '["Racing", "Commercial use", "Intentional damage"]'
) ON CONFLICT DO NOTHING;

INSERT INTO coverage_items (id, policy_id, name, coverage_type, limit_amount, deductible) VALUES
    (uuid_generate_v4(), 'b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a22', 'Collision Coverage', 'collision', 50000.00, 500.00),
    (uuid_generate_v4(), 'b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a22', 'Comprehensive Coverage', 'comprehensive', 50000.00, 250.00),
    (uuid_generate_v4(), 'b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a22', 'Liability Coverage', 'liability', 100000.00, 0.00)
ON CONFLICT DO NOTHING;

-- Log initialization
DO $$ BEGIN
    RAISE NOTICE 'Insurance Adjudicator database initialized successfully';
END $$;
