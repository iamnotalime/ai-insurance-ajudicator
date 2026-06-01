"""
Core Data Models for Insurance Adjudication System
Pydantic models for type safety and validation
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator, model_validator


class ClaimType(str, Enum):
    """Types of insurance claims"""
    AUTO = "auto"
    HOME = "home"
    HEALTH = "health"
    LIFE = "life"
    DISABILITY = "disability"
    LIABILITY = "liability"
    PROPERTY = "property"
    WORKERS_COMP = "workers_compensation"


class ClaimStatus(str, Enum):
    """Claim processing status"""
    SUBMITTED = "submitted"
    PENDING_DOCUMENTS = "pending_documents"
    UNDER_REVIEW = "under_review"
    PENDING_INVESTIGATION = "pending_investigation"
    AGENT_PROCESSING = "agent_processing"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    DENIED = "denied"
    APPEALED = "appealed"
    CLOSED = "closed"
    FRAUD_SUSPECTED = "fraud_suspected"


class Priority(str, Enum):
    """Claim priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class DecisionReason(str, Enum):
    """Standardized decision reasons"""
    POLICY_COVERAGE_VALID = "policy_coverage_valid"
    WITHIN_COVERAGE_LIMITS = "within_coverage_limits"
    DOCUMENTATION_COMPLETE = "documentation_complete"
    NO_EXCLUSIONS_APPLY = "no_exclusions_apply"
    
    COVERAGE_EXCLUDED = "coverage_excluded"
    POLICY_LAPSED = "policy_lapsed"
    DEDUCTIBLE_NOT_MET = "deductible_not_met"
    EXCEEDS_COVERAGE_LIMIT = "exceeds_coverage_limit"
    PRE_EXISTING_CONDITION = "pre_existing_condition"
    WAITING_PERIOD_ACTIVE = "waiting_period_active"
    DOCUMENTATION_INSUFFICIENT = "documentation_insufficient"
    FRAUD_INDICATORS = "fraud_indicators"
    MATERIAL_MISREPRESENTATION = "material_misrepresentation"


class Document(BaseModel):
    """Document attached to a claim"""
    id: UUID = Field(default_factory=uuid4)
    filename: str
    document_type: str  # e.g., "medical_record", "police_report", "invoice"
    content_type: str  # MIME type
    storage_path: str
    extracted_text: Optional[str] = None
    extraction_confidence: Optional[float] = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyHolder(BaseModel):
    """Policy holder information"""
    id: UUID = Field(default_factory=uuid4)
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None
    date_of_birth: date
    address: Dict[str, str] = Field(default_factory=dict)
    risk_score: Optional[float] = None
    previous_claims_count: int = 0
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"
    
    @property
    def age(self) -> int:
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )


class CoverageItem(BaseModel):
    """Individual coverage item within a policy"""
    name: str
    coverage_type: str
    limit: Decimal
    deductible: Decimal
    copay_percentage: Optional[float] = None
    exclusions: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)


class Policy(BaseModel):
    """Insurance policy details"""
    id: UUID = Field(default_factory=uuid4)
    policy_number: str
    policy_type: ClaimType
    holder: PolicyHolder
    
    effective_date: date
    expiration_date: date
    premium: Decimal
    
    coverages: List[CoverageItem] = Field(default_factory=list)
    total_coverage_limit: Decimal
    aggregate_deductible: Decimal
    
    endorsements: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    
    is_active: bool = True
    auto_renewal: bool = True
    
    @property
    def is_valid(self) -> bool:
        today = date.today()
        return self.is_active and self.effective_date <= today <= self.expiration_date
    
    @property
    def days_until_expiration(self) -> int:
        return (self.expiration_date - date.today()).days


class ClaimItem(BaseModel):
    """Individual item within a claim"""
    id: UUID = Field(default_factory=uuid4)
    description: str
    category: str
    amount_claimed: Decimal
    amount_approved: Optional[Decimal] = None
    
    date_of_loss: date
    location: Optional[str] = None
    
    supporting_documents: List[UUID] = Field(default_factory=list)
    notes: Optional[str] = None
    
    coverage_applicable: Optional[str] = None
    deductible_applied: Decimal = Decimal("0")
    
    @field_validator("amount_claimed", "amount_approved", mode="before")
    @classmethod
    def validate_decimal(cls, v):
        if v is not None:
            return Decimal(str(v))
        return v


class FraudIndicator(BaseModel):
    """Fraud detection indicator"""
    indicator_type: str
    description: str
    severity: str  # low, medium, high
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdjudicationDecision(BaseModel):
    """Final adjudication decision"""
    id: UUID = Field(default_factory=uuid4)
    decision: str  # approved, denied, partial
    confidence_score: float
    
    approved_amount: Decimal
    denied_amount: Decimal
    
    reasons: List[DecisionReason] = Field(default_factory=list)
    detailed_explanation: str
    
    coverage_analysis: Dict[str, Any] = Field(default_factory=dict)
    policy_compliance: bool = True
    
    requires_human_review: bool = False
    human_review_reasons: List[str] = Field(default_factory=list)
    
    fraud_indicators: List[FraudIndicator] = Field(default_factory=list)
    risk_assessment: Optional[Dict[str, Any]] = None
    
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_by: str = "ai_adjudicator"
    
    appeal_eligible: bool = True
    appeal_deadline: Optional[date] = None


class Claim(BaseModel):
    """Main insurance claim model"""
    id: UUID = Field(default_factory=uuid4)
    claim_number: str
    claim_type: ClaimType
    status: ClaimStatus = ClaimStatus.SUBMITTED
    priority: Priority = Priority.MEDIUM
    
    policy_id: UUID
    policy: Optional[Policy] = None
    
    claimant_id: UUID
    claimant: Optional[PolicyHolder] = None
    
    # Claim details
    date_of_loss: date
    date_reported: date = Field(default_factory=lambda: date.today())
    description: str
    location_of_loss: Optional[str] = None
    
    # Financial
    total_amount_claimed: Decimal
    items: List[ClaimItem] = Field(default_factory=list)
    
    # Documents
    documents: List[Document] = Field(default_factory=list)
    
    # Processing
    assigned_to: Optional[str] = None
    decision: Optional[AdjudicationDecision] = None
    
    # Audit trail
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processing_started_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None
    
    # Agent processing metadata
    agent_iterations: int = 0
    agent_reasoning_chain: List[Dict[str, Any]] = Field(default_factory=list)

    # Workflow error tracking
    workflow_errors: List[Dict[str, Any]] = Field(default_factory=list)
    failed_steps: List[str] = Field(default_factory=list)
    last_error_message: Optional[str] = None
    last_error_at: Optional[datetime] = None

    # Track discrepancy for fraud detection
    amount_discrepancy: Optional[Decimal] = None
    amount_discrepancy_flagged: bool = False

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.items:
            calculated_total = sum(item.amount_claimed for item in self.items)
            discrepancy = abs(calculated_total - self.total_amount_claimed)
            if discrepancy > Decimal("0.01"):
                # Flag the discrepancy instead of silently correcting
                self.amount_discrepancy = discrepancy
                self.amount_discrepancy_flagged = True
                # Log for audit trail
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Claim amount discrepancy detected: "
                    f"stated={self.total_amount_claimed}, "
                    f"calculated={calculated_total}, "
                    f"difference={discrepancy}"
                )
                # Still correct the total but keep the flag
                self.total_amount_claimed = calculated_total
        return self
    
    @property
    def processing_time_seconds(self) -> Optional[float]:
        if self.processing_started_at and self.processing_completed_at:
            return (self.processing_completed_at - self.processing_started_at).total_seconds()
        return None
    
    @property
    def days_since_submission(self) -> int:
        return (datetime.now(timezone.utc) - self.created_at).days


class AgentTask(BaseModel):
    """Task assigned to an agent"""
    id: UUID = Field(default_factory=uuid4)
    task_type: str
    claim_id: UUID
    
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[Dict[str, Any]] = None
    
    status: str = "pending"  # pending, in_progress, completed, failed
    error_message: Optional[str] = None
    
    retries: int = 0
    max_retries: int = 3
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    agent_name: str
    reasoning: Optional[str] = None
    confidence: Optional[float] = None


class AgentMessage(BaseModel):
    """Message passed between agents"""
    id: UUID = Field(default_factory=uuid4)
    from_agent: str
    to_agent: str
    message_type: str  # request, response, broadcast
    
    content: Dict[str, Any]
    context: Dict[str, Any] = Field(default_factory=dict)
    
    correlation_id: Optional[UUID] = None
    parent_message_id: Optional[UUID] = None
    
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = 300


class WorkflowState(BaseModel):
    """State of the adjudication workflow"""
    id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    
    current_step: str
    completed_steps: List[str] = Field(default_factory=list)
    pending_steps: List[str] = Field(default_factory=list)
    
    agent_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    shared_context: Dict[str, Any] = Field(default_factory=dict)
    
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    is_complete: bool = False
    final_decision: Optional[AdjudicationDecision] = None
