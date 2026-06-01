"""
Centralized Error Handler
Provides standardized error handling across the application
"""

import logging
import traceback
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, Type, TypeVar, Union
from uuid import UUID

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .exceptions import (
    AdjudicatorError,
    ClaimNotFoundError,
    PolicyNotFoundError,
    PolicyExpiredError,
    PolicyInactiveError,
    ValidationError,
    CoverageError,
    CircuitOpenError,
    AgentExecutionError,
    WorkflowError,
    ConfigurationError,
    RateLimitError,
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    ErrorSeverity,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


class ErrorResponse(BaseModel):
    """Standardized API error response."""

    error: str
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    retry_after: Optional[int] = None
    documentation_url: Optional[str] = None

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }


# Error code to HTTP status mapping
ERROR_STATUS_MAP: Dict[str, int] = {
    # Not Found errors (404)
    "ERR_1001": status.HTTP_404_NOT_FOUND,  # CLAIM_NOT_FOUND
    "ERR_1002": status.HTTP_404_NOT_FOUND,  # POLICY_NOT_FOUND
    "ERR_1003": status.HTTP_404_NOT_FOUND,  # DOCUMENT_NOT_FOUND

    # Validation errors (400)
    "ERR_2001": status.HTTP_400_BAD_REQUEST,  # INVALID_CLAIM_TYPE
    "ERR_2002": status.HTTP_400_BAD_REQUEST,  # INVALID_AMOUNT
    "ERR_2003": status.HTTP_400_BAD_REQUEST,  # INVALID_DATE
    "ERR_2004": status.HTTP_400_BAD_REQUEST,  # MISSING_REQUIRED_FIELD
    "ERR_2005": status.HTTP_400_BAD_REQUEST,  # INVALID_STATUS_TRANSITION

    # Policy errors (400/422)
    "ERR_3001": status.HTTP_422_UNPROCESSABLE_ENTITY,  # POLICY_EXPIRED
    "ERR_3002": status.HTTP_422_UNPROCESSABLE_ENTITY,  # POLICY_INACTIVE
    "ERR_3003": status.HTTP_422_UNPROCESSABLE_ENTITY,  # COVERAGE_LIMIT_EXCEEDED
    "ERR_3004": status.HTTP_422_UNPROCESSABLE_ENTITY,  # DEDUCTIBLE_NOT_MET
    "ERR_3005": status.HTTP_422_UNPROCESSABLE_ENTITY,  # POLICY_TYPE_MISMATCH

    # Authentication errors (401)
    "ERR_4001": status.HTTP_401_UNAUTHORIZED,  # INVALID_API_KEY
    "ERR_4002": status.HTTP_401_UNAUTHORIZED,  # EXPIRED_TOKEN
    "ERR_4003": status.HTTP_401_UNAUTHORIZED,  # MISSING_CREDENTIALS

    # Authorization errors (403)
    "ERR_4101": status.HTTP_403_FORBIDDEN,  # INSUFFICIENT_PERMISSIONS
    "ERR_4102": status.HTTP_403_FORBIDDEN,  # RESOURCE_ACCESS_DENIED

    # Rate limiting (429)
    "ERR_4201": status.HTTP_429_TOO_MANY_REQUESTS,  # RATE_LIMIT_EXCEEDED

    # External service errors (502/503)
    "ERR_5001": status.HTTP_503_SERVICE_UNAVAILABLE,  # LLM_SERVICE_UNAVAILABLE
    "ERR_5002": status.HTTP_502_BAD_GATEWAY,  # LLM_RESPONSE_ERROR
    "ERR_5003": status.HTTP_503_SERVICE_UNAVAILABLE,  # CIRCUIT_OPEN
    "ERR_5004": status.HTTP_503_SERVICE_UNAVAILABLE,  # DATABASE_UNAVAILABLE

    # Agent errors (500)
    "ERR_6001": status.HTTP_500_INTERNAL_SERVER_ERROR,  # AGENT_EXECUTION_FAILED
    "ERR_6002": status.HTTP_500_INTERNAL_SERVER_ERROR,  # WORKFLOW_FAILED
    "ERR_6003": status.HTTP_500_INTERNAL_SERVER_ERROR,  # ORCHESTRATION_FAILED

    # Configuration errors (500)
    "ERR_7001": status.HTTP_500_INTERNAL_SERVER_ERROR,  # CONFIGURATION_INVALID
    "ERR_7002": status.HTTP_500_INTERNAL_SERVER_ERROR,  # MISSING_CONFIGURATION
}


def exception_to_response(
    exc: Exception,
    correlation_id: Optional[str] = None,
    include_trace: bool = False,
) -> ErrorResponse:
    """
    Convert an exception to a standardized error response.

    Args:
        exc: The exception to convert
        correlation_id: Request correlation ID
        include_trace: Whether to include stack trace in details

    Returns:
        ErrorResponse object
    """
    details: Dict[str, Any] = {}

    if include_trace:
        details["trace"] = traceback.format_exc()

    if isinstance(exc, AdjudicatorError):
        details.update(exc.details)

        return ErrorResponse(
            error=exc.__class__.__name__,
            code=exc.code,
            message=exc.message,
            details=details if details else None,
            correlation_id=correlation_id or exc.correlation_id,
            retry_after=exc.retry_after,
            documentation_url=f"/docs/errors/{exc.code}",
        )

    if isinstance(exc, HTTPException):
        return ErrorResponse(
            error="HTTPException",
            code=f"HTTP_{exc.status_code}",
            message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            correlation_id=correlation_id,
        )

    # Generic exception
    return ErrorResponse(
        error=exc.__class__.__name__,
        code="ERR_0000",
        message=str(exc) or "An unexpected error occurred",
        details=details if details else None,
        correlation_id=correlation_id,
    )


def get_http_status(exc: Exception) -> int:
    """
    Get the appropriate HTTP status code for an exception.

    Args:
        exc: The exception

    Returns:
        HTTP status code
    """
    if isinstance(exc, AdjudicatorError):
        return ERROR_STATUS_MAP.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    if isinstance(exc, HTTPException):
        return exc.status_code

    # Map specific exception types
    exception_status_map: Dict[Type[Exception], int] = {
        ValueError: status.HTTP_400_BAD_REQUEST,
        TypeError: status.HTTP_400_BAD_REQUEST,
        KeyError: status.HTTP_404_NOT_FOUND,
        PermissionError: status.HTTP_403_FORBIDDEN,
        TimeoutError: status.HTTP_504_GATEWAY_TIMEOUT,
        ConnectionError: status.HTTP_503_SERVICE_UNAVAILABLE,
    }

    for exc_type, status_code in exception_status_map.items():
        if isinstance(exc, exc_type):
            return status_code

    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def handle_exception(
    request: Request,
    exc: Exception,
    include_trace: bool = False,
) -> JSONResponse:
    """
    Handle an exception and return a JSON response.

    Args:
        request: The FastAPI request
        exc: The exception
        include_trace: Whether to include stack trace

    Returns:
        JSONResponse with error details
    """
    # Get correlation ID from request state
    correlation_id = getattr(request.state, "correlation_id", None)

    # Log the error
    if isinstance(exc, AdjudicatorError):
        if exc.severity == ErrorSeverity.CRITICAL:
            logger.critical(
                f"Critical error: {exc.message}",
                extra={"correlation_id": correlation_id, "error_code": exc.code},
                exc_info=True,
            )
        elif exc.severity == ErrorSeverity.HIGH:
            logger.error(
                f"High severity error: {exc.message}",
                extra={"correlation_id": correlation_id, "error_code": exc.code},
            )
        else:
            logger.warning(
                f"Error: {exc.message}",
                extra={"correlation_id": correlation_id, "error_code": exc.code},
            )
    else:
        logger.error(
            f"Unhandled exception: {exc}",
            extra={"correlation_id": correlation_id},
            exc_info=True,
        )

    # Create response
    error_response = exception_to_response(exc, correlation_id, include_trace)
    status_code = get_http_status(exc)

    # Add retry-after header if applicable
    headers = {}
    if error_response.retry_after:
        headers["Retry-After"] = str(error_response.retry_after)

    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def create_error_handlers(app: Any) -> None:
    """
    Register exception handlers with a FastAPI app.

    Args:
        app: FastAPI application instance
    """
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):
        raise TypeError("app must be a FastAPI instance")

    @app.exception_handler(AdjudicatorError)
    async def adjudicator_error_handler(request: Request, exc: AdjudicatorError):
        return await handle_exception(request, exc)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return await handle_exception(request, exc)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return await handle_exception(request, exc, include_trace=False)


def error_boundary(
    default_error_code: str = "ERR_0000",
    default_message: str = "An unexpected error occurred",
    reraise: bool = True,
) -> Callable[[F], F]:
    """
    Decorator that provides standardized error handling for functions.

    Args:
        default_error_code: Error code to use for unexpected errors
        default_message: Default error message
        reraise: Whether to reraise the exception after logging

    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except AdjudicatorError:
                raise
            except Exception as e:
                logger.error(
                    f"Error in {func.__name__}: {e}",
                    exc_info=True,
                )
                if reraise:
                    raise AdjudicatorError(
                        message=f"{default_message}: {str(e)}",
                        code=default_error_code,
                        cause=e,
                    )
                return None

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except AdjudicatorError:
                raise
            except Exception as e:
                logger.error(
                    f"Error in {func.__name__}: {e}",
                    exc_info=True,
                )
                if reraise:
                    raise AdjudicatorError(
                        message=f"{default_message}: {str(e)}",
                        code=default_error_code,
                        cause=e,
                    )
                return None

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


# =============================================================================
# Convenience functions for raising standardized errors
# =============================================================================

def raise_not_found(
    resource_type: str,
    resource_id: Union[str, UUID],
    correlation_id: Optional[str] = None,
) -> None:
    """Raise a standardized not found error."""
    if resource_type == "claim":
        raise ClaimNotFoundError(
            claim_id=str(resource_id),
            correlation_id=correlation_id,
        )
    elif resource_type == "policy":
        raise PolicyNotFoundError(
            policy_id=str(resource_id),
            correlation_id=correlation_id,
        )
    else:
        from .exceptions import NotFoundError
        raise NotFoundError(
            message=f"{resource_type.title()} {resource_id} not found",
            code="ERR_1000",
            resource_type=resource_type,
            resource_id=str(resource_id),
            correlation_id=correlation_id,
        )


def raise_validation_error(
    message: str,
    field: Optional[str] = None,
    value: Optional[Any] = None,
    constraints: Optional[list] = None,
) -> None:
    """Raise a standardized validation error."""
    raise ValidationError(
        message=message,
        field=field,
        value=value,
        constraints=constraints or [],
    )


def raise_circuit_open(
    circuit_name: str,
    retry_after: int = 60,
    cause: Optional[Exception] = None,
) -> None:
    """Raise a circuit breaker open error."""
    raise CircuitOpenError(
        circuit_name=circuit_name,
        retry_after=retry_after,
        cause=cause,
    )


def raise_rate_limit(
    limit: int,
    window_seconds: int,
    retry_after: int,
) -> None:
    """Raise a rate limit error."""
    raise RateLimitError(
        limit=limit,
        window_seconds=window_seconds,
        retry_after=retry_after,
    )


# =============================================================================
# Result wrapper for operation outcomes
# =============================================================================

class Result(BaseModel):
    """
    Generic result wrapper for operations that may fail.
    Provides a functional approach to error handling.
    """

    success: bool
    value: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    @classmethod
    def ok(cls, value: Any = None) -> "Result":
        """Create a successful result."""
        return cls(success=True, value=value)

    @classmethod
    def fail(
        cls,
        error: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "Result":
        """Create a failed result."""
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            details=details,
        )

    @classmethod
    def from_exception(cls, exc: Exception) -> "Result":
        """Create a result from an exception."""
        if isinstance(exc, AdjudicatorError):
            return cls(
                success=False,
                error=exc.message,
                error_code=exc.code,
                details=exc.details,
            )
        return cls(
            success=False,
            error=str(exc),
            error_code="ERR_0000",
        )

    def unwrap(self) -> Any:
        """Get the value or raise an exception if failed."""
        if not self.success:
            raise AdjudicatorError(
                message=self.error or "Operation failed",
                code=self.error_code or "ERR_0000",
                details=self.details or {},
            )
        return self.value

    def unwrap_or(self, default: Any) -> Any:
        """Get the value or return default if failed."""
        return self.value if self.success else default
