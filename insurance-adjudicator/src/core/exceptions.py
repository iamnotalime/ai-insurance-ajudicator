"""
Domain-Specific Exception Hierarchy for Insurance Adjudication System
Provides structured error handling with recovery guidance
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID


class ErrorCode(str, Enum):
    """Standardized error codes for API responses and logging"""
    # General errors (1xxx)
    INTERNAL_ERROR = "ERR_1000"
    VALIDATION_ERROR = "ERR_1001"
    NOT_FOUND = "ERR_1002"
    CONFLICT = "ERR_1003"

    # Authentication/Authorization (2xxx)
    AUTHENTICATION_FAILED = "ERR_2001"
    AUTHORIZATION_DENIED = "ERR_2002"
    API_KEY_INVALID = "ERR_2003"
    API_KEY_EXPIRED = "ERR_2004"
    RATE_LIMIT_EXCEEDED = "ERR_2005"

    # Policy errors (3xxx)
    POLICY_NOT_FOUND = "ERR_3001"
    POLICY_EXPIRED = "ERR_3002"
    POLICY_INACTIVE = "ERR_3003"
    POLICY_COVERAGE_INSUFFICIENT = "ERR_3004"
    POLICY_TYPE_MISMATCH = "ERR_3005"

    # Claim errors (4xxx)
    CLAIM_NOT_FOUND = "ERR_4001"
    CLAIM_ALREADY_PROCESSED = "ERR_4002"
    CLAIM_INVALID_STATUS = "ERR_4003"
    CLAIM_AMOUNT_EXCEEDS_LIMIT = "ERR_4004"
    CLAIM_DOCUMENTS_MISSING = "ERR_4005"

    # Fraud errors (5xxx)
    FRAUD_DETECTED = "ERR_5001"
    FRAUD_INVESTIGATION_REQUIRED = "ERR_5002"
    SUSPICIOUS_PATTERN_DETECTED = "ERR_5003"

    # External service errors (6xxx)
    LLM_SERVICE_UNAVAILABLE = "ERR_6001"
    LLM_RATE_LIMITED = "ERR_6002"
    LLM_INVALID_RESPONSE = "ERR_6003"
    DATABASE_UNAVAILABLE = "ERR_6004"
    DATABASE_QUERY_FAILED = "ERR_6005"
    CIRCUIT_BREAKER_OPEN = "ERR_6006"

    # Agent errors (7xxx)
    AGENT_EXECUTION_FAILED = "ERR_7001"
    AGENT_TIMEOUT = "ERR_7002"
    AGENT_INVALID_RESPONSE = "ERR_7003"
    WORKFLOW_FAILED = "ERR_7004"
    ORCHESTRATION_ERROR = "ERR_7005"

    # Configuration errors (8xxx)
    CONFIG_MISSING = "ERR_8001"
    CONFIG_INVALID = "ERR_8002"
    FEATURE_DISABLED = "ERR_8003"


class ErrorSeverity(str, Enum):
    """Error severity levels for monitoring and alerting"""
    LOW = "low"           # Informational, no action needed
    MEDIUM = "medium"     # May require attention
    HIGH = "high"         # Requires immediate attention
    CRITICAL = "critical" # System-level failure


class AdjudicatorError(Exception):
    """
    Base exception for all insurance adjudicator errors.

    Provides structured error information including:
    - Error code for programmatic handling
    - Severity for monitoring/alerting
    - Recovery suggestions
    - Correlation ID for tracing
    - Additional context
    """

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        recoverable: bool = True,
        retry_after: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.severity = severity
        self.recoverable = recoverable
        self.retry_after = retry_after
        self.details = details or {}
        self.cause = cause
        self.correlation_id = correlation_id
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for API responses"""
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "severity": self.severity.value,
                "recoverable": self.recoverable,
                "retry_after": self.retry_after,
                "details": self.details,
                "correlation_id": self.correlation_id,
                "timestamp": self.timestamp.isoformat(),
            }
        }

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code.value}, "
            f"message={self.message!r}, "
            f"severity={self.severity.value})"
        )


# ==================== Domain Errors ====================

class DomainError(AdjudicatorError):
    """Base class for domain/business logic errors"""
    pass


class ValidationError(DomainError):
    """Input validation failed"""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        constraints: Optional[List[str]] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "field": field,
            "value": str(value) if value is not None else None,
            "constraints": constraints or [],
        })
        super().__init__(
            message=message,
            code=ErrorCode.VALIDATION_ERROR,
            severity=ErrorSeverity.LOW,
            details=details,
            **kwargs
        )
        self.field = field
        self.value = value
        self.constraints = constraints


# ==================== Not Found Errors ====================

class NotFoundError(DomainError):
    """Base class for resource not found errors"""

    def __init__(
        self,
        resource_type: str,
        resource_id: Optional[str] = None,
        code: ErrorCode = ErrorCode.NOT_FOUND,
        **kwargs
    ):
        message = f"{resource_type} not found"
        if resource_id:
            message = f"{resource_type} with ID '{resource_id}' not found"

        details = kwargs.pop("details", {})
        details.update({
            "resource_type": resource_type,
            "resource_id": resource_id,
        })

        super().__init__(
            message=message,
            code=code,
            severity=ErrorSeverity.LOW,
            recoverable=False,
            details=details,
            **kwargs
        )
        self.resource_type = resource_type
        self.resource_id = resource_id


class PolicyNotFoundError(NotFoundError):
    """Policy not found"""

    def __init__(self, policy_id: Optional[str] = None, **kwargs):
        super().__init__(
            resource_type="Policy",
            resource_id=policy_id,
            code=ErrorCode.POLICY_NOT_FOUND,
            **kwargs
        )


class ClaimNotFoundError(NotFoundError):
    """Claim not found"""

    def __init__(self, claim_id: Optional[str] = None, **kwargs):
        super().__init__(
            resource_type="Claim",
            resource_id=claim_id,
            code=ErrorCode.CLAIM_NOT_FOUND,
            **kwargs
        )


class PolicyHolderNotFoundError(NotFoundError):
    """Policy holder not found"""

    def __init__(self, holder_id: Optional[str] = None, **kwargs):
        super().__init__(
            resource_type="PolicyHolder",
            resource_id=holder_id,
            code=ErrorCode.NOT_FOUND,
            **kwargs
        )


# ==================== Authentication/Authorization Errors ====================

class AuthenticationError(AdjudicatorError):
    """Authentication failed"""

    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(
            message=message,
            code=ErrorCode.AUTHENTICATION_FAILED,
            severity=ErrorSeverity.MEDIUM,
            recoverable=True,
            **kwargs
        )


class AuthorizationError(AdjudicatorError):
    """Authorization denied"""

    def __init__(
        self,
        message: str = "Access denied",
        resource: Optional[str] = None,
        action: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "resource": resource,
            "action": action,
        })
        super().__init__(
            message=message,
            code=ErrorCode.AUTHORIZATION_DENIED,
            severity=ErrorSeverity.MEDIUM,
            recoverable=False,
            details=details,
            **kwargs
        )


class RateLimitError(AdjudicatorError):
    """Rate limit exceeded"""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        limit: Optional[int] = None,
        window_seconds: Optional[int] = None,
        retry_after: int = 60,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "limit": limit,
            "window_seconds": window_seconds,
        })
        super().__init__(
            message=message,
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            severity=ErrorSeverity.LOW,
            recoverable=True,
            retry_after=retry_after,
            details=details,
            **kwargs
        )


# ==================== External Service Errors ====================

class ExternalServiceError(AdjudicatorError):
    """Base class for external service errors"""

    def __init__(
        self,
        service_name: str,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        retry_after: Optional[int] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details["service_name"] = service_name

        super().__init__(
            message=f"{service_name}: {message}",
            code=code,
            severity=ErrorSeverity.HIGH,
            recoverable=retry_after is not None,
            retry_after=retry_after,
            details=details,
            **kwargs
        )
        self.service_name = service_name


class LLMServiceError(ExternalServiceError):
    """LLM service error"""

    def __init__(
        self,
        message: str,
        provider: str = "LLM",
        model: Optional[str] = None,
        retry_after: Optional[int] = 30,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "provider": provider,
            "model": model,
        })
        super().__init__(
            service_name=provider,
            message=message,
            code=ErrorCode.LLM_SERVICE_UNAVAILABLE,
            retry_after=retry_after,
            details=details,
            **kwargs
        )


class DatabaseError(ExternalServiceError):
    """Database error"""

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details["operation"] = operation

        super().__init__(
            service_name="Database",
            message=message,
            code=ErrorCode.DATABASE_QUERY_FAILED,
            retry_after=5,
            details=details,
            **kwargs
        )


class CircuitOpenError(ExternalServiceError):
    """Circuit breaker is open"""

    def __init__(
        self,
        circuit_name: str,
        retry_after: int = 60,
        **kwargs
    ):
        super().__init__(
            service_name=f"CircuitBreaker:{circuit_name}",
            message=f"Circuit breaker '{circuit_name}' is open",
            code=ErrorCode.CIRCUIT_BREAKER_OPEN,
            retry_after=retry_after,
            **kwargs
        )
        self.circuit_name = circuit_name


# ==================== Policy/Coverage Errors ====================

class CoverageError(DomainError):
    """Base class for coverage-related errors"""
    pass


class InsufficientCoverageError(CoverageError):
    """Coverage is insufficient for the claim"""

    def __init__(
        self,
        claimed_amount: float,
        available_coverage: float,
        coverage_type: Optional[str] = None,
        **kwargs
    ):
        message = f"Insufficient coverage: claimed ${claimed_amount:.2f}, available ${available_coverage:.2f}"
        details = kwargs.pop("details", {})
        details.update({
            "claimed_amount": claimed_amount,
            "available_coverage": available_coverage,
            "coverage_type": coverage_type,
            "shortfall": claimed_amount - available_coverage,
        })
        super().__init__(
            message=message,
            code=ErrorCode.POLICY_COVERAGE_INSUFFICIENT,
            severity=ErrorSeverity.LOW,
            recoverable=False,
            details=details,
            **kwargs
        )


class PolicyExpiredError(CoverageError):
    """Policy has expired"""

    def __init__(
        self,
        policy_id: str,
        expiration_date: Optional[str] = None,
        **kwargs
    ):
        message = f"Policy {policy_id} has expired"
        if expiration_date:
            message += f" on {expiration_date}"

        details = kwargs.pop("details", {})
        details.update({
            "policy_id": policy_id,
            "expiration_date": expiration_date,
        })
        super().__init__(
            message=message,
            code=ErrorCode.POLICY_EXPIRED,
            severity=ErrorSeverity.LOW,
            recoverable=False,
            details=details,
            **kwargs
        )


class PolicyInactiveError(CoverageError):
    """Policy is inactive"""

    def __init__(self, policy_id: str, reason: Optional[str] = None, **kwargs):
        message = f"Policy {policy_id} is inactive"
        if reason:
            message += f": {reason}"

        details = kwargs.pop("details", {})
        details.update({
            "policy_id": policy_id,
            "reason": reason,
        })
        super().__init__(
            message=message,
            code=ErrorCode.POLICY_INACTIVE,
            severity=ErrorSeverity.LOW,
            recoverable=False,
            details=details,
            **kwargs
        )


# ==================== Fraud Errors ====================

class FraudDetectionError(DomainError):
    """Fraud detection related error"""

    def __init__(
        self,
        message: str,
        claim_id: Optional[str] = None,
        risk_score: Optional[float] = None,
        indicators: Optional[List[str]] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "claim_id": claim_id,
            "risk_score": risk_score,
            "indicators": indicators or [],
        })
        super().__init__(
            message=message,
            code=ErrorCode.FRAUD_DETECTED,
            severity=ErrorSeverity.HIGH,
            recoverable=False,
            details=details,
            **kwargs
        )


# ==================== Agent/Workflow Errors ====================

class AgentError(AdjudicatorError):
    """Base class for agent-related errors"""

    def __init__(
        self,
        agent_name: str,
        message: str,
        code: ErrorCode = ErrorCode.AGENT_EXECUTION_FAILED,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details["agent_name"] = agent_name

        super().__init__(
            message=f"Agent '{agent_name}': {message}",
            code=code,
            severity=ErrorSeverity.HIGH,
            details=details,
            **kwargs
        )
        self.agent_name = agent_name


class AgentExecutionError(AgentError):
    """Agent execution failed"""

    def __init__(
        self,
        agent_name: str,
        task_type: Optional[str] = None,
        message: str = "Execution failed",
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details["task_type"] = task_type

        super().__init__(
            agent_name=agent_name,
            message=message,
            code=ErrorCode.AGENT_EXECUTION_FAILED,
            details=details,
            **kwargs
        )


class AgentTimeoutError(AgentError):
    """Agent execution timed out"""

    def __init__(
        self,
        agent_name: str,
        timeout_seconds: int,
        task_type: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "timeout_seconds": timeout_seconds,
            "task_type": task_type,
        })
        super().__init__(
            agent_name=agent_name,
            message=f"Timed out after {timeout_seconds} seconds",
            code=ErrorCode.AGENT_TIMEOUT,
            recoverable=True,
            retry_after=timeout_seconds,
            details=details,
            **kwargs
        )


class WorkflowError(AdjudicatorError):
    """Workflow orchestration error"""

    def __init__(
        self,
        message: str,
        workflow_id: Optional[str] = None,
        step: Optional[str] = None,
        completed_steps: Optional[List[str]] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "workflow_id": workflow_id,
            "failed_step": step,
            "completed_steps": completed_steps or [],
        })
        super().__init__(
            message=message,
            code=ErrorCode.WORKFLOW_FAILED,
            severity=ErrorSeverity.HIGH,
            details=details,
            **kwargs
        )


# ==================== Configuration Errors ====================

class ConfigurationError(AdjudicatorError):
    """Configuration error"""

    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        expected_type: Optional[str] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "config_key": config_key,
            "expected_type": expected_type,
        })
        super().__init__(
            message=message,
            code=ErrorCode.CONFIG_INVALID,
            severity=ErrorSeverity.CRITICAL,
            recoverable=False,
            details=details,
            **kwargs
        )
