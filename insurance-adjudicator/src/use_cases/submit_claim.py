"""
Submit Claim Use Case
Handles the business logic for submitting a new insurance claim
"""

import logging
from dataclasses import dataclass
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

from ..core.interfaces import (
    IClaimRepository, IPolicyRepository, IAuditLogger, IMetricsCollector,
    SubmitClaimRequest
)
from ..core.exceptions import (
    PolicyNotFoundError, PolicyExpiredError, PolicyInactiveError,
    ValidationError, CoverageError
)
from ..core.types import ClaimData, PolicyData, ClaimStatusEnum, PriorityEnum, ClaimTypeEnum


logger = logging.getLogger(__name__)


@dataclass
class SubmitClaimResponse:
    """Response from submit claim use case"""
    claim_id: UUID
    claim_number: str
    status: str
    message: str
    created_at: datetime


class SubmitClaimUseCase:
    """
    Use case for submitting a new insurance claim.

    Responsibilities:
    - Validate claim request
    - Verify policy exists and is active
    - Check policy coverage compatibility
    - Create and persist claim
    - Record audit trail and metrics
    - Queue for async processing
    """

    def __init__(
        self,
        claim_repository: IClaimRepository,
        policy_repository: IPolicyRepository,
        audit_logger: IAuditLogger,
        metrics_collector: IMetricsCollector,
    ):
        self._claim_repo = claim_repository
        self._policy_repo = policy_repository
        self._audit = audit_logger
        self._metrics = metrics_collector

    async def execute(self, request: SubmitClaimRequest) -> SubmitClaimResponse:
        """
        Execute the submit claim use case.

        Args:
            request: The claim submission request

        Returns:
            SubmitClaimResponse with claim details

        Raises:
            PolicyNotFoundError: If policy doesn't exist
            PolicyExpiredError: If policy has expired
            PolicyInactiveError: If policy is not active
            ValidationError: If request validation fails
            CoverageError: If claim type not covered by policy
        """
        logger.info(f"Processing claim submission for policy {request.policy_number}")

        # Step 1: Validate request
        self._validate_request(request)

        # Step 2: Get and validate policy
        policy = await self._get_and_validate_policy(request.policy_number)

        # Step 3: Check claim-policy compatibility
        self._check_compatibility(request.claim_type, policy)

        # Step 4: Generate claim number
        claim_number = self._generate_claim_number()

        # Step 5: Create claim entity
        claim = self._create_claim(request, policy, claim_number)

        # Step 6: Persist claim
        created_claim = await self._claim_repo.create(claim)

        # Step 7: Record metrics
        self._metrics.record_claim_submitted(
            claim_type=request.claim_type,
            amount=float(request.total_amount_claimed),
        )

        # Step 8: Audit log
        await self._audit.log(
            action="claim.created",
            resource_type="claim",
            resource_id=created_claim.id,
            actor_type="api_client",
            new_value={
                "claim_number": claim_number,
                "claim_type": request.claim_type,
                "amount": float(request.total_amount_claimed),
                "policy_number": request.policy_number,
            },
            metadata={
                "correlation_id": request.correlation_id,
                "ip_address": request.ip_address,
            }
        )

        logger.info(f"Claim {claim_number} created successfully")

        return SubmitClaimResponse(
            claim_id=created_claim.id,
            claim_number=claim_number,
            status=ClaimStatusEnum.SUBMITTED.value,
            message="Claim submitted successfully and queued for processing",
            created_at=created_claim.created_at or datetime.now(timezone.utc),
        )

    def _validate_request(self, request: SubmitClaimRequest) -> None:
        """Validate the claim submission request"""
        errors = []

        # Validate claim type
        try:
            ClaimTypeEnum(request.claim_type)
        except ValueError:
            errors.append(f"Invalid claim type: {request.claim_type}")

        # Validate amount
        if request.total_amount_claimed <= 0:
            errors.append("Claim amount must be positive")

        if request.total_amount_claimed > Decimal("10000000"):  # $10M limit
            errors.append("Claim amount exceeds maximum limit")

        # Validate date of loss
        if request.date_of_loss > datetime.now(timezone.utc).date():
            errors.append("Date of loss cannot be in the future")

        # Validate description
        if not request.description or len(request.description.strip()) < 10:
            errors.append("Description must be at least 10 characters")

        if errors:
            raise ValidationError(
                message="Claim submission validation failed",
                field="request",
                constraints=errors,
            )

    async def _get_and_validate_policy(self, policy_number: str) -> PolicyData:
        """Get policy and validate it's active and not expired"""
        policy = await self._policy_repo.get_by_policy_number(policy_number)

        if not policy:
            raise PolicyNotFoundError(
                policy_id=policy_number,
                correlation_id=None,
            )

        # Check if policy is active
        if not policy.is_active:
            raise PolicyInactiveError(
                policy_id=policy_number,
                reason="Policy has been cancelled or suspended",
            )

        # Check if policy has expired
        today = date.today()
        if policy.expiration_date < today:
            raise PolicyExpiredError(
                policy_id=policy_number,
                expiration_date=policy.expiration_date.isoformat(),
            )

        # Check if policy is effective yet
        if policy.effective_date > today:
            raise PolicyInactiveError(
                policy_id=policy_number,
                reason=f"Policy not effective until {policy.effective_date}",
            )

        return policy

    def _check_compatibility(self, claim_type: str, policy: PolicyData) -> None:
        """Check if claim type is compatible with policy type"""
        compatibility_map = {
            "home": {"home", "property", "liability"},
            "property": {"property", "home"},
            "auto": {"auto", "liability"},
            "health": {"health"},
            "life": {"life"},
            "disability": {"disability"},
            "liability": {"liability", "auto", "home"},
            "workers_compensation": {"workers_compensation"},
        }

        policy_type = policy.policy_type.value if hasattr(policy.policy_type, "value") else str(policy.policy_type)
        allowed_claims = compatibility_map.get(policy_type, {policy_type})

        if claim_type not in allowed_claims:
            raise CoverageError(
                message=f"Claim type '{claim_type}' is not covered by policy type '{policy_type}'",
                code="ERR_3005",  # POLICY_TYPE_MISMATCH
            )

    def _generate_claim_number(self) -> str:
        """Generate a unique claim number"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        unique_id = uuid4().hex[:8].upper()
        return f"CLM-{timestamp}-{unique_id}"

    def _create_claim(
        self,
        request: SubmitClaimRequest,
        policy: PolicyData,
        claim_number: str,
    ) -> ClaimData:
        """Create the claim data object"""
        return ClaimData(
            id=uuid4(),
            claim_number=claim_number,
            claim_type=ClaimTypeEnum(request.claim_type),
            status=ClaimStatusEnum.SUBMITTED,
            priority=PriorityEnum(request.priority),
            policy_id=policy.id,
            claimant_id=policy.holder_id,
            date_of_loss=request.date_of_loss.date() if isinstance(request.date_of_loss, datetime) else request.date_of_loss,
            date_reported=date.today(),
            description=request.description,
            total_amount_claimed=request.total_amount_claimed,
            location_of_loss=request.location_of_loss,
            policy=policy,
            claimant=policy.holder,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
