"""
Service Interfaces (Protocols) for Insurance Adjudication System
Defines contracts for dependency injection and clean architecture
"""

from abc import ABC, abstractmethod
from typing import (
    Protocol, TypeVar, Generic, Optional, List, Dict, Any,
    AsyncIterator, runtime_checkable
)
from datetime import datetime
from uuid import UUID
from decimal import Decimal

from .types import (
    ClaimData, PolicyData, DecisionData, FraudAnalysisResult,
    CoverageAnalysisResult, DocumentExtractionResult, AgentExecutionResult,
    AuditEntry, MetricLabels
)


T = TypeVar("T")
ID = TypeVar("ID")


# ==================== Repository Interfaces ====================

@runtime_checkable
class IRepository(Protocol[T, ID]):
    """Base repository interface for CRUD operations"""

    async def get_by_id(self, id: ID) -> Optional[T]:
        """Get entity by ID"""
        ...

    async def create(self, entity: T) -> T:
        """Create a new entity"""
        ...

    async def update(self, entity: T) -> T:
        """Update an existing entity"""
        ...

    async def delete(self, id: ID) -> bool:
        """Delete entity by ID"""
        ...

    async def count(self) -> int:
        """Count all entities"""
        ...


@runtime_checkable
class IClaimRepository(Protocol):
    """Repository interface for claim operations"""

    async def get_by_id(self, id: UUID) -> Optional[ClaimData]:
        """Get claim by ID"""
        ...

    async def get_by_claim_number(self, claim_number: str) -> Optional[ClaimData]:
        """Get claim by claim number"""
        ...

    async def create(self, claim: ClaimData) -> ClaimData:
        """Create a new claim"""
        ...

    async def update(self, claim: ClaimData) -> ClaimData:
        """Update an existing claim"""
        ...

    async def update_status(self, id: UUID, status: str) -> bool:
        """Update claim status"""
        ...

    async def update_decision(self, id: UUID, decision: DecisionData) -> bool:
        """Update claim with adjudication decision"""
        ...

    async def list_claims(
        self,
        status: Optional[str] = None,
        claim_type: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ClaimData]:
        """List claims with filtering"""
        ...

    async def get_pending_claims(self, limit: int = 100) -> List[ClaimData]:
        """Get claims pending processing"""
        ...

    async def get_statistics(self) -> Dict[str, Any]:
        """Get claim statistics"""
        ...


@runtime_checkable
class IPolicyRepository(Protocol):
    """Repository interface for policy operations"""

    async def get_by_id(self, id: UUID) -> Optional[PolicyData]:
        """Get policy by ID"""
        ...

    async def get_by_policy_number(self, policy_number: str) -> Optional[PolicyData]:
        """Get policy by policy number"""
        ...

    async def get_active_policies(self, holder_id: UUID) -> List[PolicyData]:
        """Get all active policies for a holder"""
        ...

    async def create(self, policy: PolicyData) -> PolicyData:
        """Create a new policy"""
        ...

    async def update(self, policy: PolicyData) -> PolicyData:
        """Update an existing policy"""
        ...


# ==================== Service Interfaces ====================

@runtime_checkable
class ILLMClient(Protocol):
    """Interface for LLM service clients"""

    @property
    def model(self) -> str:
        """Get the model name"""
        ...

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        stop_sequences: Optional[List[str]] = None,
    ) -> str:
        """Generate text completion"""
        ...

    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Generate completion with tool use"""
        ...

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Generate streaming completion"""
        ...


@runtime_checkable
class IAuditLogger(Protocol):
    """Interface for audit logging service"""

    async def log(
        self,
        action: str,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log an audit event"""
        ...

    async def get_entries_for_resource(
        self,
        resource_type: str,
        resource_id: UUID,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get audit entries for a resource"""
        ...

    async def get_entries_by_correlation_id(
        self,
        correlation_id: str,
    ) -> List[AuditEntry]:
        """Get entries by correlation ID"""
        ...


@runtime_checkable
class IMetricsCollector(Protocol):
    """Interface for metrics collection"""

    def record_http_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration: float,
    ) -> None:
        """Record HTTP request metrics"""
        ...

    def record_claim_submitted(self, claim_type: str, amount: float) -> None:
        """Record claim submission"""
        ...

    def record_claim_processed(
        self,
        claim_type: str,
        decision: str,
        required_human_review: bool,
        duration: float,
    ) -> None:
        """Record claim processing"""
        ...

    def record_agent_execution(
        self,
        agent_name: str,
        task_type: str,
        status: str,
        duration: float,
        confidence: Optional[float] = None,
    ) -> None:
        """Record agent execution"""
        ...

    def record_llm_request(
        self,
        model: str,
        status: str,
        duration: float,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        """Record LLM request"""
        ...


@runtime_checkable
class IEncryptor(Protocol):
    """Interface for encryption service"""

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext"""
        ...

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext"""
        ...

    def hash_for_lookup(self, value: str, field_name: str) -> str:
        """Create searchable hash"""
        ...


@runtime_checkable
class IRateLimiter(Protocol):
    """Interface for rate limiting"""

    async def is_allowed(
        self,
        identifier: str,
        endpoint: Optional[str] = None,
    ) -> tuple[bool, Dict[str, int]]:
        """Check if request is allowed"""
        ...

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for identifier"""
        ...


@runtime_checkable
class ICircuitBreaker(Protocol):
    """Interface for circuit breaker"""

    @property
    def state(self) -> str:
        """Get current state (closed/open/half_open)"""
        ...

    async def __aenter__(self):
        """Enter context - check if allowed"""
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit context - record success/failure"""
        ...

    def reset(self) -> None:
        """Manually reset circuit"""
        ...


# ==================== Agent Interfaces ====================

@runtime_checkable
class IAgent(Protocol):
    """Interface for AI agents"""

    @property
    def name(self) -> str:
        """Agent name"""
        ...

    @property
    def description(self) -> str:
        """Agent description"""
        ...

    @property
    def capabilities(self) -> List[str]:
        """Agent capabilities"""
        ...

    async def initialize(self) -> None:
        """Initialize agent"""
        ...

    async def execute(
        self,
        context: Any,  # AgentContext
        task: Any,     # AgentTask
    ) -> AgentExecutionResult:
        """Execute agent task"""
        ...

    async def shutdown(self) -> None:
        """Cleanup agent resources"""
        ...


@runtime_checkable
class IOrchestrator(Protocol):
    """Interface for agent orchestration"""

    async def process_claim(self, claim: ClaimData) -> Dict[str, Any]:
        """Process a single claim through the workflow"""
        ...

    async def process_batch(
        self,
        claims: List[ClaimData],
        parallel: bool = True,
    ) -> List[Dict[str, Any]]:
        """Process multiple claims"""
        ...

    async def get_workflow_status(self, workflow_id: UUID) -> Dict[str, Any]:
        """Get status of a workflow execution"""
        ...


# ==================== Use Case Interfaces ====================

class IUseCase(ABC, Generic[T]):
    """Base interface for use cases"""

    @abstractmethod
    async def execute(self, request: Any) -> T:
        """Execute the use case"""
        pass


class ISubmitClaimUseCase(IUseCase[ClaimData]):
    """Use case for submitting a claim"""

    @abstractmethod
    async def execute(self, request: "SubmitClaimRequest") -> ClaimData:
        """Submit a new claim"""
        pass


class IAdjudicateClaimUseCase(IUseCase[DecisionData]):
    """Use case for adjudicating a claim"""

    @abstractmethod
    async def execute(self, request: "AdjudicateClaimRequest") -> DecisionData:
        """Adjudicate a claim"""
        pass


# ==================== Request/Response DTOs ====================

class SubmitClaimRequest:
    """Request DTO for claim submission"""

    def __init__(
        self,
        claim_type: str,
        policy_number: str,
        date_of_loss: datetime,
        description: str,
        total_amount_claimed: Decimal,
        location_of_loss: Optional[str] = None,
        priority: str = "medium",
        items: Optional[List[Dict[str, Any]]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        claimant_email: Optional[str] = None,
        correlation_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ):
        self.claim_type = claim_type
        self.policy_number = policy_number
        self.date_of_loss = date_of_loss
        self.description = description
        self.total_amount_claimed = total_amount_claimed
        self.location_of_loss = location_of_loss
        self.priority = priority
        self.items = items or []
        self.documents = documents or []
        self.claimant_email = claimant_email
        self.correlation_id = correlation_id
        self.ip_address = ip_address


class AdjudicateClaimRequest:
    """Request DTO for claim adjudication"""

    def __init__(
        self,
        claim_id: UUID,
        force_reprocess: bool = False,
        correlation_id: Optional[str] = None,
    ):
        self.claim_id = claim_id
        self.force_reprocess = force_reprocess
        self.correlation_id = correlation_id
