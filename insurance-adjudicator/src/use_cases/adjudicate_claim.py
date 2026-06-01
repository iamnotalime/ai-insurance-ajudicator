"""
Adjudicate Claim Use Case
Handles the business logic for processing insurance claim adjudication
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

from ..core.interfaces import (
    IClaimRepository, IAuditLogger, IMetricsCollector,
    IOrchestrator, ICircuitBreaker, AdjudicateClaimRequest
)
from ..core.exceptions import (
    ClaimNotFoundError, ValidationError, CircuitOpenError,
    AgentExecutionError, WorkflowError
)
from ..core.types import ClaimData, DecisionData, ClaimStatusEnum


logger = logging.getLogger(__name__)


@dataclass
class AdjudicateClaimResponse:
    """Response from adjudicate claim use case"""
    claim_id: UUID
    workflow_id: UUID
    status: str
    decision: Optional[str]
    confidence_score: Optional[float]
    approved_amount: Optional[float]
    denied_amount: Optional[float]
    requires_human_review: bool
    human_review_reasons: List[str]
    processing_time_ms: float
    steps_completed: List[str]


class AdjudicateClaimUseCase:
    """
    Use case for adjudicating an insurance claim.

    Responsibilities:
    - Validate claim is ready for adjudication
    - Execute the multi-agent workflow
    - Handle circuit breaker for resilience
    - Record decision and update claim status
    - Audit trail and metrics
    """

    def __init__(
        self,
        claim_repository: IClaimRepository,
        orchestrator: IOrchestrator,
        circuit_breaker: ICircuitBreaker,
        audit_logger: IAuditLogger,
        metrics_collector: IMetricsCollector,
    ):
        self._claim_repo = claim_repository
        self._orchestrator = orchestrator
        self._circuit_breaker = circuit_breaker
        self._audit = audit_logger
        self._metrics = metrics_collector

    async def execute(self, request: AdjudicateClaimRequest) -> AdjudicateClaimResponse:
        """
        Execute the adjudicate claim use case.

        Args:
            request: The adjudication request

        Returns:
            AdjudicateClaimResponse with decision details

        Raises:
            ClaimNotFoundError: If claim doesn't exist
            ValidationError: If claim is not in valid state for adjudication
            CircuitOpenError: If LLM services are unavailable
            WorkflowError: If workflow execution fails
        """
        logger.info(f"Processing adjudication for claim {request.claim_id}")

        # Step 1: Get and validate claim
        claim = await self._get_and_validate_claim(request)

        # Step 2: Update claim status to processing
        await self._claim_repo.update_status(
            request.claim_id,
            ClaimStatusEnum.AGENT_PROCESSING.value,
        )

        start_time = datetime.now(timezone.utc)

        try:
            # Step 3: Execute workflow with circuit breaker protection
            result = await self._execute_with_circuit_breaker(claim)

            processing_time_ms = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds() * 1000

            # Step 4: Update claim with decision
            if result.get("decision"):
                await self._update_claim_with_decision(claim, result)

            # Step 5: Determine final status
            final_status = self._determine_final_status(result)
            await self._claim_repo.update_status(request.claim_id, final_status)

            # Step 6: Record metrics
            self._record_metrics(claim, result, processing_time_ms)

            # Step 7: Audit log
            await self._record_audit(claim, result, request.correlation_id)

            return self._build_response(
                claim_id=request.claim_id,
                result=result,
                processing_time_ms=processing_time_ms,
            )

        except Exception as e:
            # Handle failure - update claim status
            await self._claim_repo.update_status(
                request.claim_id,
                ClaimStatusEnum.UNDER_REVIEW.value,
            )

            await self._audit.log(
                action="adjudication.failed",
                resource_type="claim",
                resource_id=request.claim_id,
                actor_type="agent",
                success=False,
                error_message=str(e),
            )

            raise

    async def _get_and_validate_claim(
        self,
        request: AdjudicateClaimRequest,
    ) -> ClaimData:
        """Get claim and validate it can be adjudicated"""
        claim = await self._claim_repo.get_by_id(request.claim_id)

        if not claim:
            raise ClaimNotFoundError(claim_id=str(request.claim_id))

        # Check if claim is in valid state
        valid_states = {
            ClaimStatusEnum.SUBMITTED,
            ClaimStatusEnum.UNDER_REVIEW,
        }

        if not request.force_reprocess:
            if claim.status not in valid_states:
                raise ValidationError(
                    message=f"Claim cannot be adjudicated in status '{claim.status.value}'",
                    field="status",
                    value=claim.status.value,
                    constraints=[f"Must be one of: {[s.value for s in valid_states]}"],
                )

        return claim

    async def _execute_with_circuit_breaker(
        self,
        claim: ClaimData,
    ) -> Dict[str, Any]:
        """Execute workflow with circuit breaker protection"""
        try:
            async with self._circuit_breaker:
                result = await self._orchestrator.process_claim(claim)
                return result
        except Exception as e:
            # Check if it's a circuit breaker error
            if "circuit" in str(type(e).__name__).lower():
                raise CircuitOpenError(
                    circuit_name="llm_api",
                    retry_after=60,
                    cause=e,
                )
            raise WorkflowError(
                message=f"Workflow execution failed: {str(e)}",
                workflow_id=str(claim.id),
                cause=e,
            )

    async def _update_claim_with_decision(
        self,
        claim: ClaimData,
        result: Dict[str, Any],
    ) -> None:
        """Update claim with adjudication decision"""
        decision = result.get("decision")
        if decision:
            await self._claim_repo.update_decision(claim.id, decision)

    def _determine_final_status(self, result: Dict[str, Any]) -> str:
        """Determine the final claim status based on result"""
        if result.get("requires_human_review"):
            return ClaimStatusEnum.HUMAN_REVIEW_REQUIRED.value

        decision = result.get("decision")
        if decision:
            decision_type = decision.get("decision", "")
            if decision_type == "approved":
                return ClaimStatusEnum.APPROVED.value
            elif decision_type == "denied":
                return ClaimStatusEnum.DENIED.value
            elif decision_type == "partial":
                return ClaimStatusEnum.PARTIALLY_APPROVED.value

        return ClaimStatusEnum.UNDER_REVIEW.value

    def _record_metrics(
        self,
        claim: ClaimData,
        result: Dict[str, Any],
        processing_time_ms: float,
    ) -> None:
        """Record metrics for the adjudication"""
        decision = result.get("decision", {})
        decision_type = decision.get("decision", "unknown") if decision else "unknown"

        self._metrics.record_claim_processed(
            claim_type=claim.claim_type.value if hasattr(claim.claim_type, "value") else str(claim.claim_type),
            decision=decision_type,
            required_human_review=result.get("requires_human_review", False),
            duration=processing_time_ms / 1000,  # Convert to seconds
        )

    async def _record_audit(
        self,
        claim: ClaimData,
        result: Dict[str, Any],
        correlation_id: Optional[str],
    ) -> None:
        """Record audit log for the adjudication"""
        decision = result.get("decision", {})

        await self._audit.log(
            action="adjudication.completed",
            resource_type="claim",
            resource_id=claim.id,
            actor_type="agent",
            actor_id="decision_agent",
            new_value={
                "decision": decision.get("decision") if decision else None,
                "confidence": decision.get("confidence_score") if decision else None,
                "requires_human_review": result.get("requires_human_review"),
                "steps_completed": result.get("steps_completed", []),
            },
            metadata={"correlation_id": correlation_id},
        )

    def _build_response(
        self,
        claim_id: UUID,
        result: Dict[str, Any],
        processing_time_ms: float,
    ) -> AdjudicateClaimResponse:
        """Build the response object"""
        decision = result.get("decision", {})

        return AdjudicateClaimResponse(
            claim_id=claim_id,
            workflow_id=result.get("workflow_id", claim_id),
            status=result.get("status", "completed"),
            decision=decision.get("decision") if decision else None,
            confidence_score=decision.get("confidence_score") if decision else None,
            approved_amount=float(decision.get("approved_amount", 0)) if decision else None,
            denied_amount=float(decision.get("denied_amount", 0)) if decision else None,
            requires_human_review=result.get("requires_human_review", False),
            human_review_reasons=result.get("human_review_reasons", []),
            processing_time_ms=processing_time_ms,
            steps_completed=result.get("steps_completed", []),
        )
