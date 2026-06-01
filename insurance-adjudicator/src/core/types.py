"""
Type Definitions for Insurance Adjudication System
Provides TypedDict and dataclass definitions for type safety
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import TypedDict, Optional, List, Dict, Any, Literal
from uuid import UUID
from enum import Enum


# ==================== Enums for Type Safety ====================

class ClaimTypeEnum(str, Enum):
    AUTO = "auto"
    HOME = "home"
    HEALTH = "health"
    LIFE = "life"
    DISABILITY = "disability"
    LIABILITY = "liability"
    PROPERTY = "property"
    WORKERS_COMP = "workers_compensation"


class ClaimStatusEnum(str, Enum):
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


class PriorityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class DecisionTypeEnum(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    PARTIAL = "partial"
    PENDING_REVIEW = "pending_review"


class FraudSeverityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ==================== TypedDict Definitions ====================

class AddressDict(TypedDict, total=False):
    """Address data structure"""
    street: str
    city: str
    state: str
    zip: str
    country: str


class PolicyHolderDict(TypedDict):
    """Policy holder data structure"""
    id: str
    first_name: str
    last_name: str
    email: str
    date_of_birth: str
    phone: Optional[str]
    address: AddressDict
    risk_score: Optional[float]
    previous_claims_count: int


class CoverageItemDict(TypedDict):
    """Coverage item data structure"""
    name: str
    coverage_type: str
    limit: str  # Decimal as string
    deductible: str  # Decimal as string
    copay_percentage: Optional[float]
    exclusions: List[str]
    conditions: List[str]


class PolicyDict(TypedDict):
    """Policy data structure"""
    id: str
    policy_number: str
    policy_type: str
    holder: PolicyHolderDict
    effective_date: str
    expiration_date: str
    premium: str  # Decimal as string
    coverages: List[CoverageItemDict]
    total_coverage_limit: str
    aggregate_deductible: str
    endorsements: List[str]
    exclusions: List[str]
    is_active: bool
    auto_renewal: bool


class DocumentDict(TypedDict):
    """Document data structure"""
    id: str
    filename: str
    document_type: str
    content_type: str
    storage_path: str
    extracted_text: Optional[str]
    extraction_confidence: Optional[float]
    uploaded_at: str
    verified: bool
    metadata: Dict[str, Any]


class ClaimItemDict(TypedDict):
    """Claim item data structure"""
    id: str
    description: str
    category: str
    amount_claimed: str
    amount_approved: Optional[str]
    date_of_loss: str
    location: Optional[str]
    supporting_documents: List[str]
    notes: Optional[str]
    coverage_applicable: Optional[str]
    deductible_applied: str


class FraudIndicatorDict(TypedDict):
    """Fraud indicator data structure"""
    indicator_type: str
    description: str
    severity: str
    confidence: float
    evidence: List[str]
    detected_at: str


class DecisionDict(TypedDict):
    """Adjudication decision data structure"""
    id: str
    decision: str
    confidence_score: float
    approved_amount: str
    denied_amount: str
    reasons: List[str]
    detailed_explanation: str
    coverage_analysis: Dict[str, Any]
    policy_compliance: bool
    requires_human_review: bool
    human_review_reasons: List[str]
    fraud_indicators: List[FraudIndicatorDict]
    risk_assessment: Optional[Dict[str, Any]]
    decided_at: str
    decided_by: str
    appeal_eligible: bool
    appeal_deadline: Optional[str]


class ClaimDict(TypedDict):
    """Claim data structure"""
    id: str
    claim_number: str
    claim_type: str
    status: str
    priority: str
    policy_id: str
    policy: Optional[PolicyDict]
    claimant_id: str
    claimant: Optional[PolicyHolderDict]
    date_of_loss: str
    date_reported: str
    description: str
    location_of_loss: Optional[str]
    total_amount_claimed: str
    items: List[ClaimItemDict]
    documents: List[DocumentDict]
    assigned_to: Optional[str]
    decision: Optional[DecisionDict]
    created_at: str
    updated_at: str
    processing_started_at: Optional[str]
    processing_completed_at: Optional[str]


# ==================== Dataclass Definitions ====================

@dataclass
class ClaimData:
    """Immutable claim data object"""
    id: UUID
    claim_number: str
    claim_type: ClaimTypeEnum
    status: ClaimStatusEnum
    priority: PriorityEnum
    policy_id: UUID
    claimant_id: UUID
    date_of_loss: date
    date_reported: date
    description: str
    total_amount_claimed: Decimal
    location_of_loss: Optional[str] = None
    items: List["ClaimItemData"] = field(default_factory=list)
    documents: List["DocumentData"] = field(default_factory=list)
    assigned_to: Optional[str] = None
    decision: Optional["DecisionData"] = None
    policy: Optional["PolicyData"] = None
    claimant: Optional["PolicyHolderData"] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    processing_started_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None


@dataclass
class PolicyData:
    """Immutable policy data object"""
    id: UUID
    policy_number: str
    policy_type: ClaimTypeEnum
    holder_id: UUID
    effective_date: date
    expiration_date: date
    premium: Decimal
    total_coverage_limit: Decimal
    aggregate_deductible: Decimal
    coverages: List["CoverageItemData"] = field(default_factory=list)
    endorsements: List[str] = field(default_factory=list)
    exclusions: List[str] = field(default_factory=list)
    is_active: bool = True
    auto_renewal: bool = True
    holder: Optional["PolicyHolderData"] = None


@dataclass
class PolicyHolderData:
    """Immutable policy holder data object"""
    id: UUID
    first_name: str
    last_name: str
    email: str
    date_of_birth: date
    phone: Optional[str] = None
    address: Dict[str, str] = field(default_factory=dict)
    risk_score: Optional[float] = None
    previous_claims_count: int = 0


@dataclass
class CoverageItemData:
    """Immutable coverage item data object"""
    name: str
    coverage_type: str
    limit: Decimal
    deductible: Decimal
    copay_percentage: Optional[float] = None
    exclusions: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)


@dataclass
class DocumentData:
    """Immutable document data object"""
    id: UUID
    filename: str
    document_type: str
    content_type: str
    storage_path: str
    extracted_text: Optional[str] = None
    extraction_confidence: Optional[float] = None
    uploaded_at: Optional[datetime] = None
    verified: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimItemData:
    """Immutable claim item data object"""
    id: UUID
    description: str
    category: str
    amount_claimed: Decimal
    date_of_loss: date
    amount_approved: Optional[Decimal] = None
    location: Optional[str] = None
    supporting_documents: List[UUID] = field(default_factory=list)
    notes: Optional[str] = None
    coverage_applicable: Optional[str] = None
    deductible_applied: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class FraudIndicatorData:
    """Immutable fraud indicator data object"""
    indicator_type: str
    description: str
    severity: FraudSeverityEnum
    confidence: float
    evidence: List[str] = field(default_factory=list)
    detected_at: Optional[datetime] = None


@dataclass
class DecisionData:
    """Immutable adjudication decision data object"""
    id: UUID
    decision: DecisionTypeEnum
    confidence_score: float
    approved_amount: Decimal
    denied_amount: Decimal
    detailed_explanation: str
    reasons: List[str] = field(default_factory=list)
    coverage_analysis: Dict[str, Any] = field(default_factory=dict)
    policy_compliance: bool = True
    requires_human_review: bool = False
    human_review_reasons: List[str] = field(default_factory=list)
    fraud_indicators: List[FraudIndicatorData] = field(default_factory=list)
    risk_assessment: Optional[Dict[str, Any]] = None
    decided_at: Optional[datetime] = None
    decided_by: str = "ai_adjudicator"
    appeal_eligible: bool = True
    appeal_deadline: Optional[date] = None


# ==================== Analysis Result Types ====================

@dataclass
class FraudAnalysisResult:
    """Result of fraud detection analysis"""
    risk_score: float
    indicators: List[FraudIndicatorData]
    requires_investigation: bool
    behavioral_risk: float
    pattern_risk: float
    document_risk: float
    history_risk: float
    analysis_confidence: float
    raw_analysis: Optional[Dict[str, Any]] = None


@dataclass
class CoverageAnalysisResult:
    """Result of coverage analysis"""
    is_covered: bool
    coverage_type: Optional[str]
    coverage_limit: Decimal
    deductible: Decimal
    copay_percentage: Optional[float]
    exclusions_applicable: List[str]
    policy_valid: bool
    claim_compatible: bool
    recommended_payout: Decimal
    analysis_confidence: float
    coverage_breakdown: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentExtractionResult:
    """Result of document extraction"""
    extracted_data: Dict[str, Any]
    document_type: str
    confidence: float
    needs_manual_review: bool
    extracted_amounts: List[Decimal]
    key_dates: List[date]
    entities_found: List[str]
    raw_text: Optional[str] = None


@dataclass
class AgentExecutionResult:
    """Result of agent task execution"""
    agent_name: str
    task_type: str
    status: Literal["success", "failure", "timeout", "skipped"]
    output: Dict[str, Any]
    confidence: float
    execution_time_ms: float
    reasoning: Optional[str] = None
    error_message: Optional[str] = None
    requires_human_review: bool = False
    review_reasons: List[str] = field(default_factory=list)


# ==================== Audit Types ====================

@dataclass
class AuditEntry:
    """Audit log entry"""
    id: UUID
    timestamp: datetime
    action: str
    resource_type: str
    resource_id: Optional[UUID]
    actor_type: str
    actor_id: Optional[str]
    actor_name: Optional[str]
    correlation_id: Optional[str]
    old_value: Optional[Dict[str, Any]]
    new_value: Optional[Dict[str, Any]]
    success: bool
    error_message: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


# ==================== Metrics Types ====================

class MetricLabels(TypedDict, total=False):
    """Labels for metrics"""
    method: str
    endpoint: str
    status_code: str
    claim_type: str
    decision: str
    agent_name: str
    task_type: str
    model: str
    token_type: str
    circuit_name: str
    result: str
