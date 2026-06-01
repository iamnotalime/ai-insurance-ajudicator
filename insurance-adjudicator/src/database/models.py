"""
SQLAlchemy ORM Models for Insurance Adjudication System
Maps to Pydantic models for API layer
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import uuid4
import json

from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, Date,
    Numeric, ForeignKey, Enum as SQLEnum, JSON, Index, event
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

from ..models.claim import ClaimType, ClaimStatus, Priority


Base = declarative_base()


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps"""
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PolicyHolderDB(Base, TimestampMixin):
    """Database model for policy holders"""
    __tablename__ = "policy_holders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(20))
    date_of_birth = Column(Date, nullable=False)
    address = Column(JSONB, default={})
    risk_score = Column(Float)
    previous_claims_count = Column(Integer, default=0)

    # Encrypted PII fields (encrypted at rest)
    ssn_encrypted = Column(String(512))  # Encrypted SSN

    # Relationships
    policies = relationship("PolicyDB", back_populates="holder")
    claims_as_claimant = relationship("ClaimDB", back_populates="claimant")

    __table_args__ = (
        Index("ix_policy_holders_name", "last_name", "first_name"),
    )


class CoverageItemDB(Base):
    """Database model for coverage items within a policy"""
    __tablename__ = "coverage_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    policy_id = Column(UUID(as_uuid=True), ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(200), nullable=False)
    coverage_type = Column(String(100), nullable=False)
    limit_amount = Column(Numeric(15, 2), nullable=False)
    deductible = Column(Numeric(15, 2), nullable=False)
    copay_percentage = Column(Float)
    exclusions = Column(JSONB, default=[])
    conditions = Column(JSONB, default=[])

    # Relationships
    policy = relationship("PolicyDB", back_populates="coverages")


class PolicyDB(Base, TimestampMixin):
    """Database model for insurance policies"""
    __tablename__ = "policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    policy_number = Column(String(50), unique=True, nullable=False, index=True)
    policy_type = Column(SQLEnum(ClaimType), nullable=False)
    holder_id = Column(UUID(as_uuid=True), ForeignKey("policy_holders.id"), nullable=False)

    effective_date = Column(Date, nullable=False)
    expiration_date = Column(Date, nullable=False)
    premium = Column(Numeric(15, 2), nullable=False)

    total_coverage_limit = Column(Numeric(15, 2), nullable=False)
    aggregate_deductible = Column(Numeric(15, 2), nullable=False)

    endorsements = Column(JSONB, default=[])
    exclusions = Column(JSONB, default=[])

    is_active = Column(Boolean, default=True)
    auto_renewal = Column(Boolean, default=True)

    # Relationships
    holder = relationship("PolicyHolderDB", back_populates="policies")
    coverages = relationship("CoverageItemDB", back_populates="policy", cascade="all, delete-orphan")
    claims = relationship("ClaimDB", back_populates="policy")

    __table_args__ = (
        Index("ix_policies_effective_dates", "effective_date", "expiration_date"),
        Index("ix_policies_type_active", "policy_type", "is_active"),
    )


class DocumentDB(Base, TimestampMixin):
    """Database model for documents"""
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)

    filename = Column(String(255), nullable=False)
    document_type = Column(String(100), nullable=False)
    content_type = Column(String(100), nullable=False)
    storage_path = Column(String(500), nullable=False)

    extracted_text = Column(Text)
    extraction_confidence = Column(Float)

    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    verified = Column(Boolean, default=False)
    metadata = Column(JSONB, default={})

    # Relationships
    claim = relationship("ClaimDB", back_populates="documents")

    __table_args__ = (
        Index("ix_documents_type", "document_type"),
    )


class ClaimItemDB(Base):
    """Database model for claim items"""
    __tablename__ = "claim_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)

    description = Column(Text, nullable=False)
    category = Column(String(100), nullable=False)
    amount_claimed = Column(Numeric(15, 2), nullable=False)
    amount_approved = Column(Numeric(15, 2))

    date_of_loss = Column(Date, nullable=False)
    location = Column(String(500))

    supporting_document_ids = Column(JSONB, default=[])
    notes = Column(Text)

    coverage_applicable = Column(String(100))
    deductible_applied = Column(Numeric(15, 2), default=0)

    # Relationships
    claim = relationship("ClaimDB", back_populates="items")


class FraudIndicatorDB(Base):
    """Database model for fraud indicators"""
    __tablename__ = "fraud_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_id = Column(UUID(as_uuid=True), ForeignKey("adjudication_decisions.id", ondelete="CASCADE"), nullable=False)

    indicator_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False)  # low, medium, high
    confidence = Column(Float, nullable=False)
    evidence = Column(JSONB, default=[])
    detected_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    decision = relationship("AdjudicationDecisionDB", back_populates="fraud_indicators")


class AdjudicationDecisionDB(Base, TimestampMixin):
    """Database model for adjudication decisions"""
    __tablename__ = "adjudication_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, unique=True)

    decision = Column(String(50), nullable=False)  # approved, denied, partial
    confidence_score = Column(Float, nullable=False)

    approved_amount = Column(Numeric(15, 2), nullable=False)
    denied_amount = Column(Numeric(15, 2), nullable=False)

    reasons = Column(JSONB, default=[])
    detailed_explanation = Column(Text, nullable=False)

    coverage_analysis = Column(JSONB, default={})
    policy_compliance = Column(Boolean, default=True)

    requires_human_review = Column(Boolean, default=False)
    human_review_reasons = Column(JSONB, default=[])

    risk_assessment = Column(JSONB)

    decided_at = Column(DateTime(timezone=True), server_default=func.now())
    decided_by = Column(String(100), default="ai_adjudicator")

    appeal_eligible = Column(Boolean, default=True)
    appeal_deadline = Column(Date)

    # Relationships
    claim = relationship("ClaimDB", back_populates="decision")
    fraud_indicators = relationship("FraudIndicatorDB", back_populates="decision", cascade="all, delete-orphan")


class ClaimDB(Base, TimestampMixin):
    """Database model for insurance claims"""
    __tablename__ = "claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_number = Column(String(50), unique=True, nullable=False, index=True)
    claim_type = Column(SQLEnum(ClaimType), nullable=False)
    status = Column(SQLEnum(ClaimStatus), default=ClaimStatus.SUBMITTED, nullable=False)
    priority = Column(SQLEnum(Priority), default=Priority.MEDIUM, nullable=False)

    policy_id = Column(UUID(as_uuid=True), ForeignKey("policies.id"), nullable=False)
    claimant_id = Column(UUID(as_uuid=True), ForeignKey("policy_holders.id"), nullable=False)

    date_of_loss = Column(Date, nullable=False)
    date_reported = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    location_of_loss = Column(String(500))

    total_amount_claimed = Column(Numeric(15, 2), nullable=False)

    assigned_to = Column(String(100))

    processing_started_at = Column(DateTime(timezone=True))
    processing_completed_at = Column(DateTime(timezone=True))

    agent_iterations = Column(Integer, default=0)
    agent_reasoning_chain = Column(JSONB, default=[])

    workflow_errors = Column(JSONB, default=[])
    failed_steps = Column(JSONB, default=[])
    last_error_message = Column(Text)
    last_error_at = Column(DateTime(timezone=True))

    amount_discrepancy = Column(Numeric(15, 2))
    amount_discrepancy_flagged = Column(Boolean, default=False)

    # Relationships
    policy = relationship("PolicyDB", back_populates="claims")
    claimant = relationship("PolicyHolderDB", back_populates="claims_as_claimant")
    items = relationship("ClaimItemDB", back_populates="claim", cascade="all, delete-orphan")
    documents = relationship("DocumentDB", back_populates="claim", cascade="all, delete-orphan")
    decision = relationship("AdjudicationDecisionDB", back_populates="claim", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_claims_status", "status"),
        Index("ix_claims_type_status", "claim_type", "status"),
        Index("ix_claims_date_reported", "date_reported"),
        Index("ix_claims_policy", "policy_id"),
    )


class WorkflowExecutionDB(Base, TimestampMixin):
    """Database model for workflow execution records"""
    __tablename__ = "workflow_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id = Column(UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False)

    status = Column(String(50), nullable=False)
    current_step = Column(String(100))

    steps_completed = Column(JSONB, default=[])
    steps_failed = Column(JSONB, default=[])
    pending_steps = Column(JSONB, default=[])

    agent_results = Column(JSONB, default={})
    shared_context = Column(JSONB, default={})

    total_execution_time_ms = Column(Float, default=0)

    requires_human_review = Column(Boolean, default=False)
    human_review_reasons = Column(JSONB, default=[])

    error_message = Column(Text)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workflow_claim", "claim_id"),
        Index("ix_workflow_status", "status"),
    )


class AuditLogDB(Base):
    """Database model for audit logging"""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(UUID(as_uuid=True))

    actor_type = Column(String(50), nullable=False)  # user, agent, system
    actor_id = Column(String(100))
    actor_name = Column(String(200))

    ip_address = Column(String(45))
    user_agent = Column(String(500))

    old_value = Column(JSONB)
    new_value = Column(JSONB)

    correlation_id = Column(UUID(as_uuid=True), index=True)

    success = Column(Boolean, default=True)
    error_message = Column(Text)

    metadata = Column(JSONB, default={})

    __table_args__ = (
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_actor", "actor_type", "actor_id"),
        Index("ix_audit_action", "action"),
    )
