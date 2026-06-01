"""
Structured Logging for Insurance Adjudication System
Provides JSON-formatted logs with correlation IDs
"""

import logging
import sys
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextvars import ContextVar

from ..config.settings import settings


# Context variables for request tracking
correlation_id_context: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
request_id_context: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.

    Output format compatible with:
    - Elasticsearch / ELK Stack
    - Datadog
    - Splunk
    - Google Cloud Logging
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": settings.app_name,
            "version": settings.version,
            "environment": settings.environment.value,
        }

        # Add correlation IDs if available
        correlation_id = correlation_id_context.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id

        request_id = request_id_context.get()
        if request_id:
            log_data["request_id"] = request_id

        # Add record attributes
        if hasattr(record, "correlation_id") and record.correlation_id:
            log_data["correlation_id"] = record.correlation_id

        # Add source location
        log_data["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }

        # Add extra fields
        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "correlation_id", "message",
            ):
                try:
                    # Ensure value is JSON serializable
                    json.dumps(value)
                    extra_fields[key] = value
                except (TypeError, ValueError):
                    extra_fields[key] = str(value)

        if extra_fields:
            log_data["extra"] = extra_fields

        return json.dumps(log_data, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable formatter for development"""

    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[35m", # Magenta
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]

        correlation_id = correlation_id_context.get() or getattr(record, "correlation_id", "N/A")

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        message = f"{color}{timestamp}{reset} | {color}{record.levelname:8}{reset} | "
        message += f"{correlation_id[:8] if correlation_id != 'N/A' else 'N/A':8} | "
        message += f"{record.name:30} | {record.getMessage()}"

        if record.exc_info:
            message += f"\n{self.formatException(record.exc_info)}"

        return message


class ContextFilter(logging.Filter):
    """Filter that adds context variables to log records"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_context.get() or "N/A"
        record.request_id = request_id_context.get() or "N/A"
        return True


def setup_structured_logging(
    level: Optional[str] = None,
    json_format: Optional[bool] = None,
) -> None:
    """
    Configure structured logging for the application.

    Args:
        level: Log level (defaults to settings)
        json_format: Use JSON format (defaults to True in production)
    """
    log_level = level or settings.observability.log_level
    use_json = json_format if json_format is not None else settings.is_production

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Add context filter
    console_handler.addFilter(ContextFilter())

    # Set formatter
    if use_json:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(HumanReadableFormatter())

    root_logger.addHandler(console_handler)

    # Configure third-party loggers
    _configure_third_party_loggers()

    logging.info(
        f"Logging configured: level={log_level}, format={'JSON' if use_json else 'human-readable'}"
    )


def _configure_third_party_loggers():
    """Configure logging levels for third-party libraries"""
    # Reduce noise from verbose libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """
    Context manager for adding extra fields to logs within a scope.

    Usage:
        with LogContext(claim_id=str(claim.id), agent="fraud_detection"):
            logger.info("Processing claim")  # Will include claim_id and agent
    """

    def __init__(self, **kwargs):
        self.extra = kwargs
        self._original_factory = None

    def __enter__(self):
        self._original_factory = logging.getLogRecordFactory()

        extra = self.extra

        def record_factory(*args, **kwargs):
            record = self._original_factory(*args, **kwargs)
            for key, value in extra.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self._original_factory)
        return False


def log_operation(
    operation: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    **extra,
) -> None:
    """
    Log a business operation with structured data.

    Args:
        operation: Operation name (e.g., "claim.submitted", "decision.made")
        resource_type: Type of resource (e.g., "claim", "policy")
        resource_id: Resource identifier
        **extra: Additional fields to log
    """
    logger = logging.getLogger("operations")

    log_data = {
        "operation": operation,
    }

    if resource_type:
        log_data["resource_type"] = resource_type
    if resource_id:
        log_data["resource_id"] = resource_id

    log_data.update(extra)

    # Create log record with extra data
    logger.info(
        f"Operation: {operation}",
        extra=log_data,
    )


def log_security_event(
    event_type: str,
    success: bool,
    **extra,
) -> None:
    """
    Log a security-related event.

    Args:
        event_type: Type of security event (e.g., "auth.failed", "rate_limit.exceeded")
        success: Whether the security check passed
        **extra: Additional fields to log
    """
    logger = logging.getLogger("security")

    log_data = {
        "event_type": event_type,
        "success": success,
    }
    log_data.update(extra)

    level = logging.WARNING if not success else logging.INFO
    logger.log(
        level,
        f"Security event: {event_type}",
        extra=log_data,
    )
