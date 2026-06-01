"""
Correlation ID Middleware for Insurance Adjudication System
Provides request tracing across distributed services
"""

import logging
import contextvars
from typing import Callable, Awaitable, Optional
from uuid import uuid4, UUID

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# Context variable for correlation ID
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> Optional[str]:
    """Get the current correlation ID from context"""
    return correlation_id_var.get()


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID in context"""
    correlation_id_var.set(correlation_id)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that manages correlation IDs for request tracing.

    Features:
    - Generates or propagates correlation IDs
    - Stores ID in context for logging
    - Adds ID to response headers
    """

    HEADER_NAME = "X-Correlation-ID"
    REQUEST_ID_HEADER = "X-Request-ID"

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with correlation ID tracking"""

        # Get or generate correlation ID
        correlation_id = request.headers.get(self.HEADER_NAME)
        if not correlation_id:
            correlation_id = str(uuid4())

        # Generate unique request ID
        request_id = str(uuid4())

        # Store in context
        set_correlation_id(correlation_id)

        # Store in request state for access in handlers
        request.state.correlation_id = correlation_id
        request.state.request_id = request_id

        # Process request
        response = await call_next(request)

        # Add headers to response
        response.headers[self.HEADER_NAME] = correlation_id
        response.headers[self.REQUEST_ID_HEADER] = request_id

        return response


class CorrelationIDFilter(logging.Filter):
    """
    Logging filter that adds correlation ID to log records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        correlation_id = get_correlation_id()
        record.correlation_id = correlation_id or "N/A"
        return True


def configure_logging_with_correlation():
    """
    Configure logging to include correlation IDs.
    Call this during application startup.
    """
    # Add filter to root logger
    root_logger = logging.getLogger()
    root_logger.addFilter(CorrelationIDFilter())

    # Update formatter to include correlation ID
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(correlation_id)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    for handler in root_logger.handlers:
        handler.setFormatter(formatter)
