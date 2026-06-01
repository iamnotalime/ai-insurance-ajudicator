"""
Audit Logging Service for Insurance Adjudication System
Provides comprehensive audit trail for compliance and security
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from uuid import UUID, uuid4
from dataclasses import dataclass, field
from enum import Enum

from ..middleware.correlation import get_correlation_id
from ..config.settings import settings


logger = logging.getLogger(__name__)


def _use_database() -> bool:
    """Check if database persistence is enabled"""
    return settings.features.use_database


class AuditAction(str, Enum):
    """Standardized audit actions"""
    # Claim actions
    CLAIM_CREATED = "claim.created"
    CLAIM_UPDATED = "claim.updated"
    CLAIM_VIEWED = "claim.viewed"
    CLAIM_DELETED = "claim.deleted"
    CLAIM_STATUS_CHANGED = "claim.status_changed"

    # Decision actions
    DECISION_MADE = "decision.made"
    DECISION_OVERRIDDEN = "decision.overridden"
    DECISION_APPEALED = "decision.appealed"

    # Agent actions
    AGENT_EXECUTED = "agent.executed"
    AGENT_FAILED = "agent.failed"

    # Authentication actions
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_FAILED = "auth.failed"
    API_KEY_USED = "auth.api_key_used"

    # Data access actions
    DATA_EXPORTED = "data.exported"
    DATA_ACCESSED = "data.accessed"
    PII_ACCESSED = "pii.accessed"

    # Administrative actions
    CONFIG_CHANGED = "config.changed"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    PERMISSION_CHANGED = "permission.changed"

    # Security events
    RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "security.suspicious_activity"
    FRAUD_DETECTED = "security.fraud_detected"


class ActorType(str, Enum):
    """Types of actors that can perform actions"""
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    API_CLIENT = "api_client"


@dataclass
class AuditEntry:
    """Audit log entry"""
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    action: AuditAction = AuditAction.DATA_ACCESSED
    resource_type: str = ""
    resource_id: Optional[UUID] = None

    actor_type: ActorType = ActorType.SYSTEM
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None

    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    correlation_id: Optional[str] = None

    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None

    success: bool = True
    error_message: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    """
    Audit logging service for compliance and security.

    Features:
    - Immutable audit trail
    - Correlation ID tracking
    - PII access logging
    - Compliance-ready format (HIPAA, SOC2, GDPR)
    """

    def __init__(self):
        self._entries: List[AuditEntry] = []
        self._logger = logging.getLogger("audit")

    async def log(
        self,
        action: AuditAction,
        resource_type: str,
        resource_id: Optional[UUID] = None,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        old_value: Optional[Dict[str, Any]] = None,
        new_value: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """
        Log an audit event.

        Args:
            action: The action being audited
            resource_type: Type of resource (claim, policy, user, etc.)
            resource_id: ID of the affected resource
            actor_type: Type of actor performing the action
            actor_id: ID of the actor
            actor_name: Human-readable name of the actor
            ip_address: IP address of the request
            user_agent: User agent string
            old_value: Previous value (for changes)
            new_value: New value (for changes)
            success: Whether the action succeeded
            error_message: Error message if action failed
            metadata: Additional metadata

        Returns:
            The created audit entry
        """
        entry = AuditEntry(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=get_correlation_id(),
            old_value=self._sanitize_for_audit(old_value),
            new_value=self._sanitize_for_audit(new_value),
            success=success,
            error_message=error_message,
            metadata=metadata or {},
        )

        # Always keep in-memory copy for fast queries
        self._entries.append(entry)

        # Persist to database if enabled
        if _use_database():
            await self._persist_to_db(entry)

        # Log to structured logger
        self._log_entry(entry)

        return entry

    async def _persist_to_db(self, entry: AuditEntry) -> None:
        """Persist audit entry to database"""
        try:
            from ..database.session import DatabaseSession
            from ..database.models import AuditLogDB

            async with DatabaseSession() as session:
                db_entry = AuditLogDB(
                    id=entry.id,
                    timestamp=entry.timestamp,
                    action=entry.action.value,
                    resource_type=entry.resource_type,
                    resource_id=entry.resource_id,
                    actor_type=entry.actor_type.value,
                    actor_id=entry.actor_id,
                    actor_name=entry.actor_name,
                    ip_address=entry.ip_address,
                    user_agent=entry.user_agent,
                    correlation_id=UUID(entry.correlation_id) if entry.correlation_id else None,
                    old_value=entry.old_value,
                    new_value=entry.new_value,
                    success=entry.success,
                    error_message=entry.error_message,
                    metadata=entry.metadata,
                )
                session.add(db_entry)
        except Exception as e:
            self._logger.error(f"Failed to persist audit entry to database: {e}", exc_info=True)

    def _sanitize_for_audit(self, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Sanitize values for audit logging (redact sensitive fields)"""
        if value is None:
            return None

        sensitive_fields = {
            "password", "api_key", "secret", "token", "ssn",
            "credit_card", "cvv", "pin", "authorization",
        }

        def redact(obj):
            if isinstance(obj, dict):
                return {
                    k: "[REDACTED]" if k.lower() in sensitive_fields else redact(v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, list):
                return [redact(item) for item in obj]
            return obj

        return redact(value)

    def _log_entry(self, entry: AuditEntry) -> None:
        """Log entry to structured logger"""
        log_data = {
            "audit_id": str(entry.id),
            "action": entry.action.value,
            "resource_type": entry.resource_type,
            "resource_id": str(entry.resource_id) if entry.resource_id else None,
            "actor_type": entry.actor_type.value,
            "actor_id": entry.actor_id,
            "actor_name": entry.actor_name,
            "correlation_id": entry.correlation_id,
            "success": entry.success,
            "ip_address": entry.ip_address,
        }

        if entry.success:
            self._logger.info(
                f"AUDIT: {entry.action.value} on {entry.resource_type}",
                extra=log_data,
            )
        else:
            log_data["error_message"] = entry.error_message
            self._logger.warning(
                f"AUDIT FAILURE: {entry.action.value} on {entry.resource_type}",
                extra=log_data,
            )

    # Convenience methods for common actions

    async def log_claim_created(
        self,
        claim_id: UUID,
        claim_data: Dict[str, Any],
        actor_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AuditEntry:
        """Log claim creation"""
        return await self.log(
            action=AuditAction.CLAIM_CREATED,
            resource_type="claim",
            resource_id=claim_id,
            actor_type=ActorType.API_CLIENT,
            actor_id=actor_id,
            new_value=claim_data,
            ip_address=ip_address,
        )

    async def log_decision_made(
        self,
        claim_id: UUID,
        decision: str,
        confidence: float,
        requires_review: bool,
        agent_name: str,
    ) -> AuditEntry:
        """Log adjudication decision"""
        return await self.log(
            action=AuditAction.DECISION_MADE,
            resource_type="claim",
            resource_id=claim_id,
            actor_type=ActorType.AGENT,
            actor_id=agent_name,
            actor_name=f"AI Agent: {agent_name}",
            new_value={
                "decision": decision,
                "confidence": confidence,
                "requires_human_review": requires_review,
            },
        )

    async def log_agent_execution(
        self,
        agent_name: str,
        task_type: str,
        claim_id: UUID,
        success: bool,
        duration_ms: float,
        error_message: Optional[str] = None,
    ) -> AuditEntry:
        """Log agent task execution"""
        return await self.log(
            action=AuditAction.AGENT_EXECUTED if success else AuditAction.AGENT_FAILED,
            resource_type="claim",
            resource_id=claim_id,
            actor_type=ActorType.AGENT,
            actor_id=agent_name,
            actor_name=f"Agent: {agent_name}",
            success=success,
            error_message=error_message,
            metadata={
                "task_type": task_type,
                "duration_ms": duration_ms,
            },
        )

    async def log_fraud_detected(
        self,
        claim_id: UUID,
        fraud_indicators: List[Dict[str, Any]],
        risk_score: float,
    ) -> AuditEntry:
        """Log fraud detection"""
        return await self.log(
            action=AuditAction.FRAUD_DETECTED,
            resource_type="claim",
            resource_id=claim_id,
            actor_type=ActorType.AGENT,
            actor_id="fraud_detection_agent",
            metadata={
                "risk_score": risk_score,
                "indicator_count": len(fraud_indicators),
                "indicators": [
                    {"type": i.get("indicator_type"), "severity": i.get("severity")}
                    for i in fraud_indicators
                ],
            },
        )

    async def log_pii_access(
        self,
        resource_type: str,
        resource_id: UUID,
        fields_accessed: List[str],
        actor_id: str,
        purpose: str,
    ) -> AuditEntry:
        """Log PII data access for HIPAA/GDPR compliance"""
        return await self.log(
            action=AuditAction.PII_ACCESSED,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            metadata={
                "fields_accessed": fields_accessed,
                "purpose": purpose,
            },
        )

    async def log_auth_failure(
        self,
        ip_address: str,
        reason: str,
        user_agent: Optional[str] = None,
    ) -> AuditEntry:
        """Log authentication failure"""
        return await self.log(
            action=AuditAction.AUTH_FAILED,
            resource_type="authentication",
            actor_type=ActorType.API_CLIENT,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            error_message=reason,
        )

    async def log_rate_limit_exceeded(
        self,
        identifier: str,
        endpoint: str,
        ip_address: str,
    ) -> AuditEntry:
        """Log rate limit exceeded"""
        return await self.log(
            action=AuditAction.RATE_LIMIT_EXCEEDED,
            resource_type="rate_limit",
            actor_type=ActorType.API_CLIENT,
            actor_id=identifier,
            ip_address=ip_address,
            success=False,
            metadata={"endpoint": endpoint},
        )

    # Query methods

    def _entry_from_db(self, row) -> AuditEntry:
        """Convert a database row to an AuditEntry"""
        return AuditEntry(
            id=row.id,
            timestamp=row.timestamp,
            action=AuditAction(row.action),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            actor_type=ActorType(row.actor_type),
            actor_id=row.actor_id,
            actor_name=row.actor_name,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
            correlation_id=str(row.correlation_id) if row.correlation_id else None,
            old_value=row.old_value,
            new_value=row.new_value,
            success=row.success,
            error_message=row.error_message,
            metadata=row.metadata or {},
        )

    async def get_entries_for_resource(
        self,
        resource_type: str,
        resource_id: UUID,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get audit entries for a specific resource"""
        if _use_database():
            try:
                from ..database.session import DatabaseSession
                from ..database.models import AuditLogDB
                from sqlalchemy import select

                async with DatabaseSession() as session:
                    stmt = (
                        select(AuditLogDB)
                        .where(AuditLogDB.resource_type == resource_type)
                        .where(AuditLogDB.resource_id == resource_id)
                        .order_by(AuditLogDB.timestamp.desc())
                        .limit(limit)
                    )
                    result = await session.execute(stmt)
                    rows = result.scalars().all()
                    return [self._entry_from_db(r) for r in reversed(rows)]
            except Exception as e:
                self._logger.error(f"DB query failed, falling back to in-memory: {e}")

        return [
            e for e in self._entries
            if e.resource_type == resource_type and e.resource_id == resource_id
        ][-limit:]

    async def get_entries_by_correlation_id(
        self,
        correlation_id: str,
    ) -> List[AuditEntry]:
        """Get all audit entries for a correlation ID"""
        if _use_database():
            try:
                from ..database.session import DatabaseSession
                from ..database.models import AuditLogDB
                from sqlalchemy import select

                async with DatabaseSession() as session:
                    stmt = (
                        select(AuditLogDB)
                        .where(AuditLogDB.correlation_id == UUID(correlation_id))
                        .order_by(AuditLogDB.timestamp)
                    )
                    result = await session.execute(stmt)
                    rows = result.scalars().all()
                    return [self._entry_from_db(r) for r in rows]
            except Exception as e:
                self._logger.error(f"DB query failed, falling back to in-memory: {e}")

        return [e for e in self._entries if e.correlation_id == correlation_id]

    async def get_security_events(
        self,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get security-related audit events"""
        security_actions = {
            AuditAction.AUTH_FAILED,
            AuditAction.RATE_LIMIT_EXCEEDED,
            AuditAction.SUSPICIOUS_ACTIVITY,
            AuditAction.FRAUD_DETECTED,
        }

        if _use_database():
            try:
                from ..database.session import DatabaseSession
                from ..database.models import AuditLogDB
                from sqlalchemy import select

                async with DatabaseSession() as session:
                    stmt = (
                        select(AuditLogDB)
                        .where(AuditLogDB.action.in_([a.value for a in security_actions]))
                    )
                    if since:
                        stmt = stmt.where(AuditLogDB.timestamp >= since)
                    stmt = stmt.order_by(AuditLogDB.timestamp.desc()).limit(limit)
                    result = await session.execute(stmt)
                    rows = result.scalars().all()
                    return [self._entry_from_db(r) for r in reversed(rows)]
            except Exception as e:
                self._logger.error(f"DB query failed, falling back to in-memory: {e}")

        entries = [e for e in self._entries if e.action in security_actions]
        if since:
            entries = [e for e in entries if e.timestamp >= since]
        return entries[-limit:]


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
