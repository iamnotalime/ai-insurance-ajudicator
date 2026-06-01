"""
List Claims Use Case
Handles retrieving multiple claims with filtering and pagination
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
from uuid import UUID

from ..core.interfaces import IClaimRepository
from ..core.types import ClaimData, ClaimStatusEnum, ClaimTypeEnum, PriorityEnum


logger = logging.getLogger(__name__)


@dataclass
class ListClaimsRequest:
    """Request for listing claims"""
    status: Optional[str] = None
    claim_type: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    limit: int = 50
    offset: int = 0
    correlation_id: Optional[str] = None


@dataclass
class ListClaimsResponse:
    """Response from list claims use case"""
    claims: List[ClaimData]
    total_count: int
    limit: int
    offset: int
    has_more: bool


class ListClaimsUseCase:
    """
    Use case for listing claims with filtering.

    Responsibilities:
    - Apply filters to claim query
    - Handle pagination
    - Return standardized response
    """

    MAX_LIMIT = 100
    DEFAULT_LIMIT = 50

    def __init__(self, claim_repository: IClaimRepository):
        self._claim_repo = claim_repository

    async def execute(self, request: ListClaimsRequest) -> ListClaimsResponse:
        """
        Execute the list claims use case.

        Args:
            request: The request with filters and pagination

        Returns:
            ListClaimsResponse with claims and pagination info
        """
        logger.debug(
            f"Listing claims: status={request.status}, "
            f"type={request.claim_type}, limit={request.limit}"
        )

        # Validate and normalize limit
        limit = min(request.limit, self.MAX_LIMIT)
        if limit <= 0:
            limit = self.DEFAULT_LIMIT

        # Validate offset
        offset = max(request.offset, 0)

        # Get claims from repository
        claims = await self._claim_repo.list_claims(
            status=request.status,
            claim_type=request.claim_type,
            priority=request.priority,
            limit=limit + 1,  # Get one extra to check if there are more
            offset=offset,
        )

        # Check if there are more results
        has_more = len(claims) > limit
        if has_more:
            claims = claims[:limit]  # Remove the extra item

        # Get total count (optional, can be expensive)
        # For now, we'll estimate based on has_more
        total_count = offset + len(claims) + (1 if has_more else 0)

        return ListClaimsResponse(
            claims=claims,
            total_count=total_count,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )
