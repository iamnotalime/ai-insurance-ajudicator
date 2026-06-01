"""
Repository Pattern Implementation for Database Access
Provides clean separation between business logic and data access
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any, TypeVar, Generic
from uuid import UUID

from sqlalchemy import select, update, delete, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    ClaimDB, PolicyDB, PolicyHolderDB, DocumentDB, ClaimItemDB,
    AdjudicationDecisionDB, FraudIndicatorDB, CoverageItemDB,
    WorkflowExecutionDB, AuditLogDB
)
from ..models.claim import (
    Claim, Policy, PolicyHolder, Document, ClaimItem,
    AdjudicationDecision, FraudIndicator, CoverageItem,
    ClaimStatus, ClaimType, Priority, WorkflowState
)


logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """Base repository with common CRUD operations"""

    def __init__(self, session: AsyncSession, model_class: type):
        self.session = session
        self.model_class = model_class

    async def get_by_id(self, id: UUID) -> Optional[T]:
        """Get entity by ID"""
        result = await self.session.execute(
            select(self.model_class).where(self.model_class.id == id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, limit: int = 100, offset: int = 0) -> List[T]:
        """Get all entities with pagination"""
        result = await self.session.execute(
            select(self.model_class).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def create(self, entity: T) -> T:
        """Create a new entity"""
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, id: UUID) -> bool:
        """Delete entity by ID"""
        result = await self.session.execute(
            delete(self.model_class).where(self.model_class.id == id)
        )
        return result.rowcount > 0

    async def count(self) -> int:
        """Count all entities"""
        result = await self.session.execute(
            select(func.count()).select_from(self.model_class)
        )
        return result.scalar() or 0


class ClaimRepository(BaseRepository[ClaimDB]):
    """Repository for claim operations"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, ClaimDB)

    async def get_by_id_with_relations(self, id: UUID) -> Optional[ClaimDB]:
        """Get claim with all related data"""
        result = await self.session.execute(
            select(ClaimDB)
            .options(
                selectinload(ClaimDB.policy).selectinload(PolicyDB.holder),
                selectinload(ClaimDB.policy).selectinload(PolicyDB.coverages),
                selectinload(ClaimDB.claimant),
                selectinload(ClaimDB.items),
                selectinload(ClaimDB.documents),
                selectinload(ClaimDB.decision).selectinload(AdjudicationDecisionDB.fraud_indicators),
            )
            .where(ClaimDB.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_claim_number(self, claim_number: str) -> Optional[ClaimDB]:
        """Get claim by claim number"""
        result = await self.session.execute(
            select(ClaimDB).where(ClaimDB.claim_number == claim_number)
        )
        return result.scalar_one_or_none()

    async def list_claims(
        self,
        status: Optional[ClaimStatus] = None,
        claim_type: Optional[ClaimType] = None,
        priority: Optional[Priority] = None,
        assigned_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ClaimDB]:
        """List claims with filtering"""
        query = select(ClaimDB)

        conditions = []
        if status:
            conditions.append(ClaimDB.status == status)
        if claim_type:
            conditions.append(ClaimDB.claim_type == claim_type)
        if priority:
            conditions.append(ClaimDB.priority == priority)
        if assigned_to:
            conditions.append(ClaimDB.assigned_to == assigned_to)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(ClaimDB.created_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_pending_claims(self, limit: int = 100) -> List[ClaimDB]:
        """Get claims pending processing"""
        result = await self.session.execute(
            select(ClaimDB)
            .where(
                ClaimDB.status.in_([
                    ClaimStatus.SUBMITTED,
                    ClaimStatus.UNDER_REVIEW,
                ])
            )
            .order_by(ClaimDB.priority.desc(), ClaimDB.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(self, id: UUID, status: ClaimStatus) -> bool:
        """Update claim status"""
        result = await self.session.execute(
            update(ClaimDB)
            .where(ClaimDB.id == id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def update_decision(self, id: UUID, decision: AdjudicationDecisionDB) -> bool:
        """Update claim with decision"""
        claim = await self.get_by_id(id)
        if not claim:
            return False

        claim.decision = decision
        claim.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return True

    async def get_statistics(self) -> Dict[str, Any]:
        """Get claim statistics"""
        # Total claims by status
        status_counts = await self.session.execute(
            select(ClaimDB.status, func.count(ClaimDB.id))
            .group_by(ClaimDB.status)
        )
        by_status = {str(row[0].value): row[1] for row in status_counts}

        # Total claims by type
        type_counts = await self.session.execute(
            select(ClaimDB.claim_type, func.count(ClaimDB.id))
            .group_by(ClaimDB.claim_type)
        )
        by_type = {str(row[0].value): row[1] for row in type_counts}

        # Average processing time
        avg_time = await self.session.execute(
            select(
                func.avg(
                    func.extract('epoch', ClaimDB.processing_completed_at - ClaimDB.processing_started_at)
                ) * 1000
            )
            .where(
                and_(
                    ClaimDB.processing_started_at.isnot(None),
                    ClaimDB.processing_completed_at.isnot(None),
                )
            )
        )
        avg_processing_ms = avg_time.scalar() or 0

        # Total amount claimed and approved
        totals = await self.session.execute(
            select(
                func.sum(ClaimDB.total_amount_claimed),
                func.count(ClaimDB.id)
            )
        )
        total_row = totals.one()

        return {
            "claims_by_status": by_status,
            "claims_by_type": by_type,
            "average_processing_time_ms": float(avg_processing_ms),
            "total_amount_claimed": float(total_row[0] or 0),
            "total_claims": total_row[1],
        }

    def pydantic_to_db(self, claim: Claim) -> ClaimDB:
        """Convert Pydantic Claim to database model"""
        return ClaimDB(
            id=claim.id,
            claim_number=claim.claim_number,
            claim_type=claim.claim_type,
            status=claim.status,
            priority=claim.priority,
            policy_id=claim.policy_id,
            claimant_id=claim.claimant_id,
            date_of_loss=claim.date_of_loss,
            date_reported=claim.date_reported,
            description=claim.description,
            location_of_loss=claim.location_of_loss,
            total_amount_claimed=claim.total_amount_claimed,
            assigned_to=claim.assigned_to,
            processing_started_at=claim.processing_started_at,
            processing_completed_at=claim.processing_completed_at,
            agent_iterations=claim.agent_iterations,
            agent_reasoning_chain=claim.agent_reasoning_chain,
            workflow_errors=claim.workflow_errors,
            failed_steps=claim.failed_steps,
            last_error_message=claim.last_error_message,
            last_error_at=claim.last_error_at,
            amount_discrepancy=claim.amount_discrepancy,
            amount_discrepancy_flagged=claim.amount_discrepancy_flagged,
        )

    def db_to_pydantic(self, db_claim: ClaimDB) -> Claim:
        """Convert database model to Pydantic Claim"""
        policy = None
        if db_claim.policy:
            policy = self._policy_db_to_pydantic(db_claim.policy)

        claimant = None
        if db_claim.claimant:
            claimant = self._holder_db_to_pydantic(db_claim.claimant)

        items = [self._item_db_to_pydantic(item) for item in db_claim.items]
        documents = [self._doc_db_to_pydantic(doc) for doc in db_claim.documents]

        decision = None
        if db_claim.decision:
            decision = self._decision_db_to_pydantic(db_claim.decision)

        return Claim(
            id=db_claim.id,
            claim_number=db_claim.claim_number,
            claim_type=db_claim.claim_type,
            status=db_claim.status,
            priority=db_claim.priority,
            policy_id=db_claim.policy_id,
            policy=policy,
            claimant_id=db_claim.claimant_id,
            claimant=claimant,
            date_of_loss=db_claim.date_of_loss,
            date_reported=db_claim.date_reported,
            description=db_claim.description,
            location_of_loss=db_claim.location_of_loss,
            total_amount_claimed=db_claim.total_amount_claimed,
            items=items,
            documents=documents,
            assigned_to=db_claim.assigned_to,
            decision=decision,
            created_at=db_claim.created_at,
            updated_at=db_claim.updated_at,
            processing_started_at=db_claim.processing_started_at,
            processing_completed_at=db_claim.processing_completed_at,
            agent_iterations=db_claim.agent_iterations,
            agent_reasoning_chain=db_claim.agent_reasoning_chain,
            workflow_errors=db_claim.workflow_errors,
            failed_steps=db_claim.failed_steps,
            last_error_message=db_claim.last_error_message,
            last_error_at=db_claim.last_error_at,
            amount_discrepancy=db_claim.amount_discrepancy,
            amount_discrepancy_flagged=db_claim.amount_discrepancy_flagged,
        )

    def _policy_db_to_pydantic(self, db_policy: PolicyDB) -> Policy:
        """Convert PolicyDB to Pydantic Policy"""
        coverages = [
            CoverageItem(
                name=c.name,
                coverage_type=c.coverage_type,
                limit=c.limit_amount,
                deductible=c.deductible,
                copay_percentage=c.copay_percentage,
                exclusions=c.exclusions or [],
                conditions=c.conditions or [],
            )
            for c in db_policy.coverages
        ]

        holder = self._holder_db_to_pydantic(db_policy.holder) if db_policy.holder else None

        return Policy(
            id=db_policy.id,
            policy_number=db_policy.policy_number,
            policy_type=db_policy.policy_type,
            holder=holder,
            effective_date=db_policy.effective_date,
            expiration_date=db_policy.expiration_date,
            premium=db_policy.premium,
            coverages=coverages,
            total_coverage_limit=db_policy.total_coverage_limit,
            aggregate_deductible=db_policy.aggregate_deductible,
            endorsements=db_policy.endorsements or [],
            exclusions=db_policy.exclusions or [],
            is_active=db_policy.is_active,
            auto_renewal=db_policy.auto_renewal,
        )

    def _holder_db_to_pydantic(self, db_holder: PolicyHolderDB) -> PolicyHolder:
        """Convert PolicyHolderDB to Pydantic PolicyHolder"""
        return PolicyHolder(
            id=db_holder.id,
            first_name=db_holder.first_name,
            last_name=db_holder.last_name,
            email=db_holder.email,
            phone=db_holder.phone,
            date_of_birth=db_holder.date_of_birth,
            address=db_holder.address or {},
            risk_score=db_holder.risk_score,
            previous_claims_count=db_holder.previous_claims_count,
        )

    def _item_db_to_pydantic(self, db_item: ClaimItemDB) -> ClaimItem:
        """Convert ClaimItemDB to Pydantic ClaimItem"""
        return ClaimItem(
            id=db_item.id,
            description=db_item.description,
            category=db_item.category,
            amount_claimed=db_item.amount_claimed,
            amount_approved=db_item.amount_approved,
            date_of_loss=db_item.date_of_loss,
            location=db_item.location,
            supporting_documents=db_item.supporting_document_ids or [],
            notes=db_item.notes,
            coverage_applicable=db_item.coverage_applicable,
            deductible_applied=db_item.deductible_applied or Decimal("0"),
        )

    def _doc_db_to_pydantic(self, db_doc: DocumentDB) -> Document:
        """Convert DocumentDB to Pydantic Document"""
        return Document(
            id=db_doc.id,
            filename=db_doc.filename,
            document_type=db_doc.document_type,
            content_type=db_doc.content_type,
            storage_path=db_doc.storage_path,
            extracted_text=db_doc.extracted_text,
            extraction_confidence=db_doc.extraction_confidence,
            uploaded_at=db_doc.uploaded_at,
            verified=db_doc.verified,
            metadata=db_doc.metadata or {},
        )

    def _decision_db_to_pydantic(self, db_decision: AdjudicationDecisionDB) -> AdjudicationDecision:
        """Convert AdjudicationDecisionDB to Pydantic AdjudicationDecision"""
        fraud_indicators = [
            FraudIndicator(
                indicator_type=f.indicator_type,
                description=f.description,
                severity=f.severity,
                confidence=f.confidence,
                evidence=f.evidence or [],
                detected_at=f.detected_at,
            )
            for f in db_decision.fraud_indicators
        ]

        return AdjudicationDecision(
            id=db_decision.id,
            decision=db_decision.decision,
            confidence_score=db_decision.confidence_score,
            approved_amount=db_decision.approved_amount,
            denied_amount=db_decision.denied_amount,
            reasons=db_decision.reasons or [],
            detailed_explanation=db_decision.detailed_explanation,
            coverage_analysis=db_decision.coverage_analysis or {},
            policy_compliance=db_decision.policy_compliance,
            requires_human_review=db_decision.requires_human_review,
            human_review_reasons=db_decision.human_review_reasons or [],
            fraud_indicators=fraud_indicators,
            risk_assessment=db_decision.risk_assessment,
            decided_at=db_decision.decided_at,
            decided_by=db_decision.decided_by,
            appeal_eligible=db_decision.appeal_eligible,
            appeal_deadline=db_decision.appeal_deadline,
        )


class PolicyRepository(BaseRepository[PolicyDB]):
    """Repository for policy operations"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, PolicyDB)

    async def get_by_policy_number(self, policy_number: str) -> Optional[PolicyDB]:
        """Get policy by policy number"""
        result = await self.session.execute(
            select(PolicyDB)
            .options(
                selectinload(PolicyDB.holder),
                selectinload(PolicyDB.coverages),
            )
            .where(PolicyDB.policy_number == policy_number)
        )
        return result.scalar_one_or_none()

    async def get_active_policies(self, holder_id: UUID) -> List[PolicyDB]:
        """Get all active policies for a holder"""
        result = await self.session.execute(
            select(PolicyDB)
            .where(
                and_(
                    PolicyDB.holder_id == holder_id,
                    PolicyDB.is_active == True,
                )
            )
        )
        return list(result.scalars().all())


class AuditLogRepository(BaseRepository[AuditLogDB]):
    """Repository for audit log operations"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, AuditLogDB)

    async def log_action(
        self,
        action: str,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[UUID] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditLogDB:
        """Create an audit log entry"""
        log = AuditLogDB(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            old_value=old_value,
            new_value=new_value,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            error_message=error_message,
            metadata=metadata or {},
        )
        return await self.create(log)

    async def get_logs_for_resource(
        self,
        resource_type: str,
        resource_id: UUID,
        limit: int = 100,
    ) -> List[AuditLogDB]:
        """Get audit logs for a specific resource"""
        result = await self.session.execute(
            select(AuditLogDB)
            .where(
                and_(
                    AuditLogDB.resource_type == resource_type,
                    AuditLogDB.resource_id == resource_id,
                )
            )
            .order_by(AuditLogDB.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_logs_by_correlation_id(self, correlation_id: UUID) -> List[AuditLogDB]:
        """Get all logs with a specific correlation ID"""
        result = await self.session.execute(
            select(AuditLogDB)
            .where(AuditLogDB.correlation_id == correlation_id)
            .order_by(AuditLogDB.timestamp.asc())
        )
        return list(result.scalars().all())
