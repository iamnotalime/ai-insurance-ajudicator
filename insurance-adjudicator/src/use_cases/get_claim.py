"""
Get Claim Use Case
Handles retrieving a single claim by ID
"""

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from ..core.interfaces import IClaimRepository, IAuditLogger
from ..core.exceptions import ClaimNotFoundError
from ..core.types import ClaimData


logger = logging.getLogger(__name__)


@dataclass
class GetClaimRequest:
    """Request for getting a claim"""
    claim_id: UUID
    include_policy: bool = True
    include_decision: bool = True
    correlation_id: Optional[str] = None
    actor_id: Optional[str] = None


@dataclass
class GetClaimResponse:
    """Response from get claim use case"""
    claim: ClaimData
    found: bool = True


class GetClaimUseCase:
    """
    Use case for retrieving a claim by ID.

    Responsibilities:
    - Retrieve claim from repository
    - Optionally include related data
    - Audit access for compliance
    """

    def __init__(
        self,
        claim_repository: IClaimRepository,
        audit_logger: IAuditLogger,
    ):
        self._claim_repo = claim_repository
        self._audit = audit_logger

    async def execute(self, request: GetClaimRequest) -> GetClaimResponse:
        """
        Execute the get claim use case.

        Args:
            request: The request with claim ID

        Returns:
            GetClaimResponse with claim data

        Raises:
            ClaimNotFoundError: If claim doesn't exist
        """
        logger.debug(f"Retrieving claim {request.claim_id}")

        # Get claim from repository
        claim = await self._claim_repo.get_by_id(request.claim_id)

        if not claim:
            raise ClaimNotFoundError(
                claim_id=str(request.claim_id),
                correlation_id=request.correlation_id,
            )

        # Audit the access
        await self._audit.log(
            action="claim.viewed",
            resource_type="claim",
            resource_id=request.claim_id,
            actor_type="api_client",
            actor_id=request.actor_id,
            metadata={"correlation_id": request.correlation_id},
        )

        return GetClaimResponse(claim=claim)
