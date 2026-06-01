"""Initial schema

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    op.execute("CREATE TYPE claimtype AS ENUM ('auto', 'home', 'health', 'life', 'disability', 'liability', 'property', 'workers_compensation')")
    op.execute("CREATE TYPE claimstatus AS ENUM ('submitted', 'pending_documents', 'under_review', 'pending_investigation', 'agent_processing', 'human_review_required', 'approved', 'partially_approved', 'denied', 'appealed', 'closed', 'fraud_suspected')")
    op.execute("CREATE TYPE priority AS ENUM ('low', 'medium', 'high', 'urgent')")

    # Create policy_holders table
    op.create_table(
        'policy_holders',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name', sa.String(100), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('phone', sa.String(20)),
        sa.Column('date_of_birth', sa.Date(), nullable=False),
        sa.Column('address', postgresql.JSONB(), default={}),
        sa.Column('risk_score', sa.Float()),
        sa.Column('previous_claims_count', sa.Integer(), default=0),
        sa.Column('ssn_encrypted', sa.String(512)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_policy_holders_email', 'policy_holders', ['email'])
    op.create_index('ix_policy_holders_name', 'policy_holders', ['last_name', 'first_name'])

    # Create policies table
    op.create_table(
        'policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('policy_number', sa.String(50), unique=True, nullable=False),
        sa.Column('policy_type', sa.Enum('auto', 'home', 'health', 'life', 'disability', 'liability', 'property', 'workers_compensation', name='claimtype'), nullable=False),
        sa.Column('holder_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policy_holders.id'), nullable=False),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('expiration_date', sa.Date(), nullable=False),
        sa.Column('premium', sa.Numeric(15, 2), nullable=False),
        sa.Column('total_coverage_limit', sa.Numeric(15, 2), nullable=False),
        sa.Column('aggregate_deductible', sa.Numeric(15, 2), nullable=False),
        sa.Column('endorsements', postgresql.JSONB(), default=[]),
        sa.Column('exclusions', postgresql.JSONB(), default=[]),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('auto_renewal', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_policies_policy_number', 'policies', ['policy_number'])
    op.create_index('ix_policies_effective_dates', 'policies', ['effective_date', 'expiration_date'])
    op.create_index('ix_policies_type_active', 'policies', ['policy_type', 'is_active'])

    # Create coverage_items table
    op.create_table(
        'coverage_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('policy_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('coverage_type', sa.String(100), nullable=False),
        sa.Column('limit_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('deductible', sa.Numeric(15, 2), nullable=False),
        sa.Column('copay_percentage', sa.Float()),
        sa.Column('exclusions', postgresql.JSONB(), default=[]),
        sa.Column('conditions', postgresql.JSONB(), default=[]),
    )

    # Create claims table
    op.create_table(
        'claims',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('claim_number', sa.String(50), unique=True, nullable=False),
        sa.Column('claim_type', sa.Enum('auto', 'home', 'health', 'life', 'disability', 'liability', 'property', 'workers_compensation', name='claimtype'), nullable=False),
        sa.Column('status', sa.Enum('submitted', 'pending_documents', 'under_review', 'pending_investigation', 'agent_processing', 'human_review_required', 'approved', 'partially_approved', 'denied', 'appealed', 'closed', 'fraud_suspected', name='claimstatus'), default='submitted', nullable=False),
        sa.Column('priority', sa.Enum('low', 'medium', 'high', 'urgent', name='priority'), default='medium', nullable=False),
        sa.Column('policy_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policies.id'), nullable=False),
        sa.Column('claimant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('policy_holders.id'), nullable=False),
        sa.Column('date_of_loss', sa.Date(), nullable=False),
        sa.Column('date_reported', sa.Date(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('location_of_loss', sa.String(500)),
        sa.Column('total_amount_claimed', sa.Numeric(15, 2), nullable=False),
        sa.Column('assigned_to', sa.String(100)),
        sa.Column('processing_started_at', sa.DateTime(timezone=True)),
        sa.Column('processing_completed_at', sa.DateTime(timezone=True)),
        sa.Column('agent_iterations', sa.Integer(), default=0),
        sa.Column('agent_reasoning_chain', postgresql.JSONB(), default=[]),
        sa.Column('workflow_errors', postgresql.JSONB(), default=[]),
        sa.Column('failed_steps', postgresql.JSONB(), default=[]),
        sa.Column('last_error_message', sa.Text()),
        sa.Column('last_error_at', sa.DateTime(timezone=True)),
        sa.Column('amount_discrepancy', sa.Numeric(15, 2)),
        sa.Column('amount_discrepancy_flagged', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_claims_claim_number', 'claims', ['claim_number'])
    op.create_index('ix_claims_status', 'claims', ['status'])
    op.create_index('ix_claims_type_status', 'claims', ['claim_type', 'status'])
    op.create_index('ix_claims_date_reported', 'claims', ['date_reported'])
    op.create_index('ix_claims_policy', 'claims', ['policy_id'])

    # Create claim_items table
    op.create_table(
        'claim_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(100), nullable=False),
        sa.Column('amount_claimed', sa.Numeric(15, 2), nullable=False),
        sa.Column('amount_approved', sa.Numeric(15, 2)),
        sa.Column('date_of_loss', sa.Date(), nullable=False),
        sa.Column('location', sa.String(500)),
        sa.Column('supporting_document_ids', postgresql.JSONB(), default=[]),
        sa.Column('notes', sa.Text()),
        sa.Column('coverage_applicable', sa.String(100)),
        sa.Column('deductible_applied', sa.Numeric(15, 2), default=0),
    )

    # Create documents table
    op.create_table(
        'documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('document_type', sa.String(100), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('storage_path', sa.String(500), nullable=False),
        sa.Column('extracted_text', sa.Text()),
        sa.Column('extraction_confidence', sa.Float()),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('verified', sa.Boolean(), default=False),
        sa.Column('metadata', postgresql.JSONB(), default={}),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_documents_type', 'documents', ['document_type'])

    # Create adjudication_decisions table
    op.create_table(
        'adjudication_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('claims.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('decision', sa.String(50), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('approved_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('denied_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('reasons', postgresql.JSONB(), default=[]),
        sa.Column('detailed_explanation', sa.Text(), nullable=False),
        sa.Column('coverage_analysis', postgresql.JSONB(), default={}),
        sa.Column('policy_compliance', sa.Boolean(), default=True),
        sa.Column('requires_human_review', sa.Boolean(), default=False),
        sa.Column('human_review_reasons', postgresql.JSONB(), default=[]),
        sa.Column('risk_assessment', postgresql.JSONB()),
        sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('decided_by', sa.String(100), default='ai_adjudicator'),
        sa.Column('appeal_eligible', sa.Boolean(), default=True),
        sa.Column('appeal_deadline', sa.Date()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Create fraud_indicators table
    op.create_table(
        'fraud_indicators',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('decision_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('adjudication_decisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('indicator_type', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('evidence', postgresql.JSONB(), default=[]),
        sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create workflow_executions table
    op.create_table(
        'workflow_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('claims.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('current_step', sa.String(100)),
        sa.Column('steps_completed', postgresql.JSONB(), default=[]),
        sa.Column('steps_failed', postgresql.JSONB(), default=[]),
        sa.Column('pending_steps', postgresql.JSONB(), default=[]),
        sa.Column('agent_results', postgresql.JSONB(), default={}),
        sa.Column('shared_context', postgresql.JSONB(), default={}),
        sa.Column('total_execution_time_ms', sa.Float(), default=0),
        sa.Column('requires_human_review', sa.Boolean(), default=False),
        sa.Column('human_review_reasons', postgresql.JSONB(), default=[]),
        sa.Column('error_message', sa.Text()),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index('ix_workflow_claim', 'workflow_executions', ['claim_id'])
    op.create_index('ix_workflow_status', 'workflow_executions', ['status'])

    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_type', sa.String(100), nullable=False),
        sa.Column('resource_id', postgresql.UUID(as_uuid=True)),
        sa.Column('actor_type', sa.String(50), nullable=False),
        sa.Column('actor_id', sa.String(100)),
        sa.Column('actor_name', sa.String(200)),
        sa.Column('ip_address', sa.String(45)),
        sa.Column('user_agent', sa.String(500)),
        sa.Column('old_value', postgresql.JSONB()),
        sa.Column('new_value', postgresql.JSONB()),
        sa.Column('correlation_id', postgresql.UUID(as_uuid=True)),
        sa.Column('success', sa.Boolean(), default=True),
        sa.Column('error_message', sa.Text()),
        sa.Column('metadata', postgresql.JSONB(), default={}),
    )
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])
    op.create_index('ix_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])
    op.create_index('ix_audit_actor', 'audit_logs', ['actor_type', 'actor_id'])
    op.create_index('ix_audit_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_correlation', 'audit_logs', ['correlation_id'])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('audit_logs')
    op.drop_table('workflow_executions')
    op.drop_table('fraud_indicators')
    op.drop_table('adjudication_decisions')
    op.drop_table('documents')
    op.drop_table('claim_items')
    op.drop_table('claims')
    op.drop_table('coverage_items')
    op.drop_table('policies')
    op.drop_table('policy_holders')

    # Drop enum types
    op.execute('DROP TYPE priority')
    op.execute('DROP TYPE claimstatus')
    op.execute('DROP TYPE claimtype')
