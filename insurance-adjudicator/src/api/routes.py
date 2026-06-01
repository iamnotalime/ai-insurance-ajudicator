"""
FastAPI REST API for Insurance Adjudication System
Enterprise-ready API with authentication, rate limiting, observability, and persistence
"""

import json
import logging
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

import asyncio

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query, status, Request, Response, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, field_validator

import uvicorn

from ..config.settings import settings
from ..models.claim import (
    Claim, ClaimType, ClaimStatus, Priority, Policy, PolicyHolder,
    CoverageItem, ClaimItem, Document, AdjudicationDecision
)
from ..agents.orchestrator import AgentOrchestrator, AdaptiveOrchestrator, OrchestrationResult
from ..agents.base import agent_registry
from ..agents.specialized import (
    DocumentExtractionAgent, PolicyAnalysisAgent,
    FraudDetectionAgent, DecisionAgent
)
from ..services.llm_client import create_llm_client
from ..services.audit import get_audit_logger, AuditAction, ActorType
from ..services.redis_service import (
    init_redis, close_redis, is_redis_available,
    get_idempotency_result, set_idempotency_result,
    enqueue_claim, redis_health_check,
)
from ..middleware.authentication import get_current_user, require_roles, AuthenticatedUser

# Database imports
from ..database.session import get_db, init_db, close_db, DatabaseSession, DatabaseHealthCheck
from ..database.repository import ClaimRepository, PolicyRepository, AuditLogRepository

# Middleware imports
from ..middleware.rate_limiting import RateLimitMiddleware, RateLimiter, get_rate_limiter
from ..middleware.security_headers import SecurityHeadersMiddleware
from ..middleware.correlation import CorrelationMiddleware, get_correlation_id

# Observability imports
from ..observability.tracing import setup_tracing, instrument_fastapi, trace_operation
from ..observability.metrics import setup_metrics, get_metrics_collector, get_metrics_endpoint
from ..observability.logging import setup_structured_logging

# Utility imports
from ..utils.circuit_breaker import get_llm_circuit_breaker, CircuitBreakerError

# Core domain imports for standardized error handling
from ..core import (
    generate_claim_number,
    create_error_handlers,
    raise_not_found,
    raise_validation_error,
    raise_circuit_open,
    ClaimNotFoundError,
    PolicyNotFoundError,
    CircuitOpenError,
)


logger = logging.getLogger(__name__)

# Graceful shutdown state
_shutting_down = False

# Dead-letter queue for failed background tasks
_dead_letter_queue: List[Dict[str, Any]] = []


# Request/Response Models — Typed input validation
class ClaimItemRequest(BaseModel):
    """Typed request model for claim items"""
    description: str = Field(..., min_length=1, max_length=1000)
    category: str = Field(..., min_length=1, max_length=100)
    amount_claimed: Decimal = Field(..., gt=0)
    date_of_loss: date
    location: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("amount_claimed")
    @classmethod
    def validate_amount(cls, v):
        if v > Decimal("10000000"):
            raise ValueError("Amount exceeds maximum allowed value")
        return v


class DocumentRequest(BaseModel):
    """Typed request model for documents"""
    filename: str = Field(..., min_length=1, max_length=500)
    document_type: str = Field(..., min_length=1, max_length=100)
    content_type: str = Field(..., min_length=1, max_length=100)
    storage_path: str = Field(..., min_length=1)


class ClaimSubmissionRequest(BaseModel):
    """Request model for submitting a new claim"""
    claim_type: ClaimType
    policy_number: str = Field(..., min_length=1, max_length=50)
    date_of_loss: date
    description: str = Field(..., min_length=10, max_length=5000)
    total_amount_claimed: Decimal = Field(..., gt=0)
    location_of_loss: Optional[str] = Field(default=None, max_length=500)
    priority: Priority = Priority.MEDIUM

    items: List[ClaimItemRequest] = Field(default_factory=list)
    documents: List[DocumentRequest] = Field(default_factory=list)

    claimant_email: Optional[str] = None

    @field_validator("total_amount_claimed")
    @classmethod
    def validate_total_amount(cls, v):
        if v > Decimal("50000000"):
            raise ValueError("Total amount exceeds maximum allowed value")
        return v


class ClaimResponse(BaseModel):
    """Response model for claim data"""
    id: UUID
    claim_number: str
    claim_type: ClaimType
    status: ClaimStatus
    priority: Priority

    date_of_loss: date
    date_reported: date
    description: str
    total_amount_claimed: Decimal

    decision: Optional[Dict[str, Any]] = None

    created_at: datetime
    updated_at: datetime
    processing_time_ms: Optional[float] = None

    class Config:
        from_attributes = True


class AdjudicationResponse(BaseModel):
    """Response model for adjudication results"""
    claim_id: UUID
    workflow_id: UUID
    status: str

    decision: Optional[Dict[str, Any]] = None

    steps_completed: List[str]
    processing_time_ms: float

    requires_human_review: bool
    human_review_reasons: List[str]


class BatchSubmissionRequest(BaseModel):
    """Request for batch claim submission"""
    claims: List[ClaimSubmissionRequest]
    process_async: bool = True


class BatchResponse(BaseModel):
    """Response for batch operations"""
    batch_id: UUID
    total_claims: int
    status: str
    results: Optional[List[AdjudicationResponse]] = None


class PaginatedResponse(BaseModel):
    """Paginated response wrapper"""
    items: List[ClaimResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    environment: str
    timestamp: datetime
    database: Optional[str] = None
    redis: Optional[str] = None


class MetricsResponse(BaseModel):
    """Metrics response"""
    total_claims_processed: int
    average_processing_time_ms: float
    approval_rate: float
    human_review_rate: float
    claims_by_type: Dict[str, int]
    claims_by_status: Dict[str, int]


# In-memory fallback storage (for development without database)
_claims_db: Dict[UUID, Claim] = {}
_policies_db: Dict[str, Policy] = {}
_orchestration_results: Dict[UUID, OrchestrationResult] = {}


def _use_database() -> bool:
    """Check if database should be used"""
    return settings.features.use_database or settings.database.password != "" or settings.is_production


# Dependency injection
async def get_orchestrator() -> AgentOrchestrator:
    """Get the agent orchestrator with circuit breaker"""
    llm_client = create_llm_client()
    return AdaptiveOrchestrator(llm_client=llm_client)


# Lifespan management
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    logger.info("Starting Insurance Adjudicator API...")

    # Initialize observability
    setup_structured_logging()
    setup_tracing()
    setup_metrics()

    # Initialize Redis
    redis_ok = await init_redis()
    if redis_ok:
        logger.info("Redis connected — using distributed rate limiting and caching")
    else:
        logger.warning("Redis unavailable — using in-memory fallbacks")

    # Initialize Redis-backed rate limiter (falls back to in-memory if Redis unavailable)
    await get_rate_limiter()

    # Initialize database if configured
    if _use_database():
        try:
            await init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            if settings.is_production:
                raise
    else:
        logger.warning(
            "Running without database - using in-memory storage. "
            "All data will be lost on restart."
        )

    # Initialize agents
    llm_client = create_llm_client()

    agents = [
        DocumentExtractionAgent(llm_client=llm_client),
        PolicyAnalysisAgent(llm_client=llm_client),
        FraudDetectionAgent(llm_client=llm_client),
        DecisionAgent(llm_client=llm_client),
    ]

    for agent in agents:
        agent_registry.register(agent)

    await agent_registry.initialize_all()

    # Create sample policy for demo (in-memory only)
    if not _use_database():
        sample_policy = Policy(
            policy_number="POL-2024-001",
            policy_type=ClaimType.AUTO,
            holder=PolicyHolder(
                first_name="John",
                last_name="Doe",
                email="john.doe@example.com",
                date_of_birth=date(1985, 5, 15),
                address={"street": "123 Main St", "city": "Anytown", "state": "CA", "zip": "90210"},
            ),
            effective_date=date(2024, 1, 1),
            expiration_date=date(2025, 1, 1),
            premium=Decimal("1200.00"),
            coverages=[
                CoverageItem(
                    name="Collision Coverage",
                    coverage_type="collision",
                    limit=Decimal("50000.00"),
                    deductible=Decimal("500.00"),
                ),
                CoverageItem(
                    name="Comprehensive Coverage",
                    coverage_type="comprehensive",
                    limit=Decimal("50000.00"),
                    deductible=Decimal("250.00"),
                ),
                CoverageItem(
                    name="Liability Coverage",
                    coverage_type="liability",
                    limit=Decimal("100000.00"),
                    deductible=Decimal("0.00"),
                ),
            ],
            total_coverage_limit=Decimal("200000.00"),
            aggregate_deductible=Decimal("500.00"),
            exclusions=["Racing", "Commercial use", "Intentional damage"],
        )
        _policies_db[sample_policy.policy_number] = sample_policy

    logger.info("API started successfully")

    yield

    # Graceful shutdown with drain period
    global _shutting_down
    _shutting_down = True
    logger.info("Shutting down — draining active requests (5s)...")
    await asyncio.sleep(5)  # Allow in-flight requests to complete

    await agent_registry.shutdown_all()
    await close_redis()
    if _use_database():
        await close_db()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Insurance AI Adjudicator",
    description="Enterprise Agentic AI system for automated insurance claim adjudication",
    version=settings.version,
    lifespan=lifespan,
)

# Register standardized exception handlers
create_error_handlers(app)

# Add middleware (order matters - last added is first executed)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else settings.security.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationMiddleware)
app.add_middleware(RateLimitMiddleware)

# Instrument for tracing
if settings.observability.enable_tracing:
    instrument_fastapi(app)


# Health endpoints
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Health check endpoint"""
    db_status = None
    if _use_database():
        db_health = await DatabaseHealthCheck.check()
        db_status = db_health.get("status")

    redis_status = None
    redis_health = await redis_health_check()
    redis_status = redis_health.get("status")

    return HealthResponse(
        status="healthy",
        version=settings.version,
        environment=settings.environment.value,
        timestamp=datetime.now(timezone.utc),
        database=db_status,
        redis=redis_status,
    )


@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness check for Kubernetes"""
    if _shutting_down:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is shutting down"
        )

    agents = agent_registry.get_all()
    if len(agents) < 4:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Not all agents initialized"
        )

    # Check database if configured
    if _use_database():
        db_health = await DatabaseHealthCheck.check()
        if db_health.get("status") != "healthy":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database not ready"
            )

    return {"status": "ready", "agents": len(agents)}


@app.get("/metrics", tags=["Observability"])
async def prometheus_metrics():
    """Prometheus metrics endpoint"""
    metrics_data, content_type = get_metrics_endpoint()
    return Response(content=metrics_data, media_type=content_type)


# Claim endpoints
@app.post(
    "/api/v1/claims",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Claims"]
)
async def submit_claim(
    request: Request,
    claim_request: ClaimSubmissionRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Submit a new insurance claim for adjudication"""
    # Idempotency check (Redis-backed with in-memory fallback)
    if x_idempotency_key:
        cached = await get_idempotency_result(x_idempotency_key)
        if cached is not None:
            return ClaimResponse(**json.loads(cached))

    metrics = get_metrics_collector()
    audit_logger = get_audit_logger()

    with trace_operation("submit_claim", {"claim_type": claim_request.claim_type.value}):
        # Look up policy
        if _use_database():
            async with DatabaseSession() as session:
                policy_repo = PolicyRepository(session)
                policy_db = await policy_repo.get_by_policy_number(claim_request.policy_number)
                if not policy_db:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Policy {claim_request.policy_number} not found"
                    )
                # Convert to Pydantic for processing
                policy = ClaimRepository(session)._policy_db_to_pydantic(policy_db)
        else:
            policy = _policies_db.get(claim_request.policy_number)
            if not policy:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Policy {claim_request.policy_number} not found"
                )

        # Create claim
        claim = Claim(
            claim_number=generate_claim_number(),
            claim_type=claim_request.claim_type,
            policy_id=policy.id,
            policy=policy,
            claimant_id=policy.holder.id,
            claimant=policy.holder,
            date_of_loss=claim_request.date_of_loss,
            description=claim_request.description,
            total_amount_claimed=claim_request.total_amount_claimed,
            location_of_loss=claim_request.location_of_loss,
            priority=claim_request.priority,
            items=[ClaimItem(**item.model_dump()) for item in claim_request.items] if claim_request.items else [],
            documents=[Document(**doc.model_dump()) for doc in claim_request.documents] if claim_request.documents else [],
        )

        # Store claim
        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claim_db = claim_repo.pydantic_to_db(claim)
                await claim_repo.create(claim_db)
        else:
            _claims_db[claim.id] = claim

        # Record metrics
        metrics.record_claim_submitted(
            claim_type=claim.claim_type.value,
            amount=float(claim.total_amount_claimed)
        )

        # Audit log
        await audit_logger.log_claim_created(
            claim_id=claim.id,
            claim_data={
                "claim_number": claim.claim_number,
                "claim_type": claim.claim_type.value,
                "amount": float(claim.total_amount_claimed),
            },
            ip_address=request.client.host if request.client else None,
        )

        # Queue for processing: try Redis queue first, fall back to background task
        enqueued = await enqueue_claim(claim.id)
        if not enqueued:
            background_tasks.add_task(process_claim_with_retry, claim.id)

        logger.info(f"Claim {claim.claim_number} submitted for processing")

        response = ClaimResponse(
            id=claim.id,
            claim_number=claim.claim_number,
            claim_type=claim.claim_type,
            status=claim.status,
            priority=claim.priority,
            date_of_loss=claim.date_of_loss,
            date_reported=claim.date_reported,
            description=claim.description,
            total_amount_claimed=claim.total_amount_claimed,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )

        # Cache idempotency result (Redis-backed)
        if x_idempotency_key:
            await set_idempotency_result(x_idempotency_key, response.model_dump_json())

        return response


@app.get("/api/v1/claims/{claim_id}", response_model=ClaimResponse, tags=["Claims"])
async def get_claim(
    claim_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get claim details by ID"""
    with trace_operation("get_claim", {"claim_id": str(claim_id)}):
        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claim_db = await claim_repo.get_by_id_with_relations(claim_id)
                if not claim_db:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Claim {claim_id} not found"
                    )
                claim = claim_repo.db_to_pydantic(claim_db)
        else:
            claim = _claims_db.get(claim_id)
            if not claim:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Claim {claim_id} not found"
                )

        return ClaimResponse(
            id=claim.id,
            claim_number=claim.claim_number,
            claim_type=claim.claim_type,
            status=claim.status,
            priority=claim.priority,
            date_of_loss=claim.date_of_loss,
            date_reported=claim.date_reported,
            description=claim.description,
            total_amount_claimed=claim.total_amount_claimed,
            decision=claim.decision.model_dump() if claim.decision else None,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
            processing_time_ms=claim.processing_time_seconds * 1000 if claim.processing_time_seconds else None,
        )


@app.get("/api/v1/claims", response_model=PaginatedResponse, tags=["Claims"])
async def list_claims(
    claim_status: Optional[ClaimStatus] = Query(default=None, alias="status"),
    claim_type: Optional[ClaimType] = None,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List claims with optional filtering and pagination metadata"""
    with trace_operation("list_claims"):
        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claims_db = await claim_repo.list_claims(
                    status=claim_status,
                    claim_type=claim_type,
                    limit=limit,
                    offset=offset,
                )
                claims = [claim_repo.db_to_pydantic(c) for c in claims_db]
                # Get total count for pagination
                all_claims = await claim_repo.list_claims(
                    status=claim_status,
                    claim_type=claim_type,
                    limit=100000,
                    offset=0,
                )
                total = len(all_claims)
        else:
            all_claims = list(_claims_db.values())
            if claim_status:
                all_claims = [c for c in all_claims if c.status == claim_status]
            if claim_type:
                all_claims = [c for c in all_claims if c.claim_type == claim_type]
            total = len(all_claims)
            claims = all_claims[offset:offset + limit]

        claim_responses = [
            ClaimResponse(
                id=c.id,
                claim_number=c.claim_number,
                claim_type=c.claim_type,
                status=c.status,
                priority=c.priority,
                date_of_loss=c.date_of_loss,
                date_reported=c.date_reported,
                description=c.description,
                total_amount_claimed=c.total_amount_claimed,
                decision=c.decision.model_dump() if c.decision else None,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in claims
        ]

        return PaginatedResponse(
            items=claim_responses,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        )


@app.post("/api/v1/claims/{claim_id}/adjudicate", response_model=AdjudicationResponse, tags=["Claims"])
async def adjudicate_claim(
    claim_id: UUID,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Manually trigger adjudication for a claim"""
    metrics = get_metrics_collector()
    audit_logger = get_audit_logger()
    circuit_breaker = get_llm_circuit_breaker()

    with trace_operation("adjudicate_claim", {"claim_id": str(claim_id)}):
        # Get claim
        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claim_db = await claim_repo.get_by_id_with_relations(claim_id)
                if not claim_db:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Claim {claim_id} not found"
                    )
                claim = claim_repo.db_to_pydantic(claim_db)
        else:
            claim = _claims_db.get(claim_id)
            if not claim:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Claim {claim_id} not found"
                )

        if claim.status not in [ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Claim is already processed (status: {claim.status})"
            )

        # Process claim with circuit breaker protection
        try:
            async with circuit_breaker:
                start_time = datetime.now(timezone.utc)
                result = await orchestrator.process_claim(claim)
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        except CircuitBreakerError as e:
            logger.error(f"Circuit breaker open: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Service temporarily unavailable. Retry after {e.retry_after:.0f} seconds.",
                headers={"Retry-After": str(int(e.retry_after))}
            )

        # Store result
        _orchestration_results[result.workflow_id] = result

        # Update claim with decision
        if result.decision:
            claim.decision = result.decision
            if _use_database():
                async with DatabaseSession() as session:
                    claim_repo = ClaimRepository(session)
                    await claim_repo.update_status(claim_id, claim.status)
            else:
                _claims_db[claim_id] = claim

        # Record metrics
        metrics.record_claim_processed(
            claim_type=claim.claim_type.value,
            decision=result.decision.decision if result.decision else "error",
            required_human_review=result.requires_human_review,
            duration=duration,
        )

        # Audit log
        if result.decision:
            await audit_logger.log_decision_made(
                claim_id=claim_id,
                decision=result.decision.decision,
                confidence=result.decision.confidence_score,
                requires_review=result.requires_human_review,
                agent_name="decision_agent",
            )

        return AdjudicationResponse(
            claim_id=claim_id,
            workflow_id=result.workflow_id,
            status=result.status,
            decision=result.decision.model_dump() if result.decision else None,
            steps_completed=result.steps_completed,
            processing_time_ms=result.total_execution_time_ms,
            requires_human_review=result.requires_human_review,
            human_review_reasons=result.human_review_reasons,
        )


# Batch processing
@app.post("/api/v1/claims/batch", response_model=BatchResponse, tags=["Batch"])
async def submit_batch(
    request: BatchSubmissionRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Submit multiple claims for batch processing"""
    batch_id = uuid4()

    if request.process_async:
        background_tasks.add_task(
            process_batch_async,
            batch_id,
            request.claims
        )

        return BatchResponse(
            batch_id=batch_id,
            total_claims=len(request.claims),
            status="queued",
        )
    else:
        results = await process_batch_sync(request.claims)

        return BatchResponse(
            batch_id=batch_id,
            total_claims=len(request.claims),
            status="completed",
            results=results,
        )


# Metrics endpoint
@app.get("/api/v1/metrics", response_model=MetricsResponse, tags=["Metrics"])
async def get_api_metrics(user: AuthenticatedUser = Depends(get_current_user)):
    """Get system metrics"""
    with trace_operation("get_metrics"):
        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                stats = await claim_repo.get_statistics()

                total_processed = sum(
                    count for status_val, count in stats["claims_by_status"].items()
                    if status_val not in ["submitted", "under_review"]
                )

                approved = stats["claims_by_status"].get("approved", 0) + \
                          stats["claims_by_status"].get("partially_approved", 0)
                approval_rate = approved / total_processed if total_processed > 0 else 0

                human_review = stats["claims_by_status"].get("human_review_required", 0)
                human_rate = human_review / stats["total_claims"] if stats["total_claims"] > 0 else 0

                return MetricsResponse(
                    total_claims_processed=total_processed,
                    average_processing_time_ms=stats["average_processing_time_ms"],
                    approval_rate=approval_rate,
                    human_review_rate=human_rate,
                    claims_by_type=stats["claims_by_type"],
                    claims_by_status=stats["claims_by_status"],
                )
        else:
            claims = list(_claims_db.values())

            total_processed = len([c for c in claims if c.status not in [ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW]])

            processing_times = [
                c.processing_time_seconds * 1000
                for c in claims
                if c.processing_time_seconds
            ]
            avg_time = sum(processing_times) / len(processing_times) if processing_times else 0

            approved = len([c for c in claims if c.status in [ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_APPROVED]])
            approval_rate = approved / total_processed if total_processed > 0 else 0

            human_review = len([c for c in claims if c.status == ClaimStatus.HUMAN_REVIEW_REQUIRED])
            human_rate = human_review / len(claims) if claims else 0

            claims_by_type = {}
            for c in claims:
                key = c.claim_type.value
                claims_by_type[key] = claims_by_type.get(key, 0) + 1

            claims_by_status = {}
            for c in claims:
                key = c.status.value
                claims_by_status[key] = claims_by_status.get(key, 0) + 1

            return MetricsResponse(
                total_claims_processed=total_processed,
                average_processing_time_ms=avg_time,
                approval_rate=approval_rate,
                human_review_rate=human_rate,
                claims_by_type=claims_by_type,
                claims_by_status=claims_by_status,
            )


# Background tasks with retry and dead-letter queue
MAX_RETRIES = 3


async def process_claim_with_retry(claim_id: UUID, attempt: int = 1):
    """Process a claim with exponential backoff retry and dead-letter queue"""
    try:
        await process_claim_async(claim_id)
    except Exception as e:
        if attempt < MAX_RETRIES:
            delay = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
            logger.warning(
                f"Claim {claim_id} processing failed (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s: {e}"
            )
            await asyncio.sleep(delay)
            await process_claim_with_retry(claim_id, attempt + 1)
        else:
            logger.error(
                f"Claim {claim_id} processing failed after {MAX_RETRIES} attempts, "
                f"sending to dead-letter queue: {e}"
            )
            _dead_letter_queue.append({
                "claim_id": str(claim_id),
                "error": str(e),
                "attempts": attempt,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


async def process_claim_async(claim_id: UUID):
    """Process a claim asynchronously"""
    metrics = get_metrics_collector()
    audit_logger = get_audit_logger()
    circuit_breaker = get_llm_circuit_breaker()

    # Get claim
    if _use_database():
        async with DatabaseSession() as session:
            claim_repo = ClaimRepository(session)
            claim_db = await claim_repo.get_by_id_with_relations(claim_id)
            if not claim_db:
                logger.error(f"Claim {claim_id} not found for async processing")
                return
            claim = claim_repo.db_to_pydantic(claim_db)
    else:
        claim = _claims_db.get(claim_id)
        if not claim:
            logger.error(f"Claim {claim_id} not found for async processing")
            return

    try:
        async with circuit_breaker:
            start_time = datetime.now(timezone.utc)

            llm_client = create_llm_client()
            orchestrator = AdaptiveOrchestrator(llm_client=llm_client)
            result = await orchestrator.process_claim(claim)

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        _orchestration_results[result.workflow_id] = result

        if result.decision:
            claim.decision = result.decision

            if _use_database():
                async with DatabaseSession() as session:
                    claim_repo = ClaimRepository(session)
                    await claim_repo.update_status(claim_id, claim.status)
            else:
                _claims_db[claim_id] = claim

        # Record metrics
        metrics.record_claim_processed(
            claim_type=claim.claim_type.value,
            decision=result.decision.decision if result.decision else "error",
            required_human_review=result.requires_human_review,
            duration=duration,
        )

        logger.info(f"Claim {claim.claim_number} processed: {result.status}")

    except CircuitBreakerError as e:
        logger.error(f"Circuit breaker prevented processing of claim {claim_id}: {e}")
        claim.status = ClaimStatus.UNDER_REVIEW
        claim.last_error_message = str(e)

    except Exception as e:
        logger.error(f"Failed to process claim {claim_id}: {e}")
        claim.status = ClaimStatus.UNDER_REVIEW
        claim.last_error_message = str(e)

        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                await claim_repo.update_status(claim_id, ClaimStatus.UNDER_REVIEW)


async def process_batch_async(batch_id: UUID, claims_data: List[ClaimSubmissionRequest]):
    """Process a batch of claims asynchronously"""
    logger.info(f"Starting batch {batch_id} with {len(claims_data)} claims")

    for req in claims_data:
        if _use_database():
            async with DatabaseSession() as session:
                policy_repo = PolicyRepository(session)
                policy_db = await policy_repo.get_by_policy_number(req.policy_number)
                if not policy_db:
                    continue
                policy = ClaimRepository(session)._policy_db_to_pydantic(policy_db)
        else:
            policy = _policies_db.get(req.policy_number)
            if not policy:
                continue

        claim = Claim(
            claim_number=generate_claim_number(),
            claim_type=req.claim_type,
            policy_id=policy.id,
            policy=policy,
            claimant_id=policy.holder.id,
            claimant=policy.holder,
            date_of_loss=req.date_of_loss,
            description=req.description,
            total_amount_claimed=req.total_amount_claimed,
        )

        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claim_db = claim_repo.pydantic_to_db(claim)
                await claim_repo.create(claim_db)
        else:
            _claims_db[claim.id] = claim

        await process_claim_async(claim.id)

    logger.info(f"Batch {batch_id} completed")


async def process_batch_sync(claims_data: List[ClaimSubmissionRequest]) -> List[AdjudicationResponse]:
    """Process a batch of claims synchronously"""
    results = []

    for req in claims_data:
        if _use_database():
            async with DatabaseSession() as session:
                policy_repo = PolicyRepository(session)
                policy_db = await policy_repo.get_by_policy_number(req.policy_number)
                if not policy_db:
                    continue
                policy = ClaimRepository(session)._policy_db_to_pydantic(policy_db)
        else:
            policy = _policies_db.get(req.policy_number)
            if not policy:
                continue

        claim = Claim(
            claim_number=generate_claim_number(),
            claim_type=req.claim_type,
            policy_id=policy.id,
            policy=policy,
            claimant_id=policy.holder.id,
            claimant=policy.holder,
            date_of_loss=req.date_of_loss,
            description=req.description,
            total_amount_claimed=req.total_amount_claimed,
        )

        if _use_database():
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                claim_db = claim_repo.pydantic_to_db(claim)
                await claim_repo.create(claim_db)
        else:
            _claims_db[claim.id] = claim

        llm_client = create_llm_client()
        orchestrator = AdaptiveOrchestrator(llm_client=llm_client)
        result = await orchestrator.process_claim(claim)

        if result.decision:
            claim.decision = result.decision

        results.append(AdjudicationResponse(
            claim_id=claim.id,
            workflow_id=result.workflow_id,
            status=result.status,
            decision=result.decision.model_dump() if result.decision else None,
            steps_completed=result.steps_completed,
            processing_time_ms=result.total_execution_time_ms,
            requires_human_review=result.requires_human_review,
            human_review_reasons=result.human_review_reasons,
        ))

    return results


def run_server():
    """Run the API server"""
    uvicorn.run(
        "src.api.routes:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
        workers=4 if settings.is_production else 1,
    )


if __name__ == "__main__":
    run_server()
