"""
OpenTelemetry Tracing for Insurance Adjudication System
Provides distributed tracing across services
"""

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Optional, Dict, Any, Callable

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace import Status, StatusCode, Span
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from ..config.settings import settings


logger = logging.getLogger(__name__)

_tracer: Optional[trace.Tracer] = None


def setup_tracing() -> None:
    """
    Initialize OpenTelemetry tracing.

    Call this during application startup.
    """
    global _tracer

    if not settings.observability.enable_tracing:
        logger.info("Tracing is disabled")
        return

    # Create resource with service information
    resource = Resource.create({
        ResourceAttributes.SERVICE_NAME: settings.app_name,
        ResourceAttributes.SERVICE_VERSION: settings.version,
        ResourceAttributes.DEPLOYMENT_ENVIRONMENT: settings.environment.value,
    })

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add exporters based on configuration
    if settings.observability.jaeger_endpoint:
        # Export to Jaeger/OTLP collector
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.observability.jaeger_endpoint,
            insecure=not settings.is_production,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info(f"Tracing exporter configured: {settings.observability.jaeger_endpoint}")
    elif settings.is_development:
        # Console exporter for development
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("Tracing exporter: console (development mode)")

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    # Create tracer
    _tracer = trace.get_tracer(__name__, settings.version)

    logger.info("OpenTelemetry tracing initialized")


def get_tracer() -> trace.Tracer:
    """Get the application tracer"""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer(__name__)
    return _tracer


def instrument_fastapi(app) -> None:
    """Instrument FastAPI application"""
    if settings.observability.enable_tracing:
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI instrumented for tracing")


def instrument_sqlalchemy(engine) -> None:
    """Instrument SQLAlchemy engine"""
    if settings.observability.enable_tracing:
        SQLAlchemyInstrumentor().instrument(engine=engine)
        logger.info("SQLAlchemy instrumented for tracing")


def instrument_httpx() -> None:
    """Instrument HTTPX client for outgoing HTTP requests"""
    if settings.observability.enable_tracing:
        HTTPXClientInstrumentor().instrument()
        logger.info("HTTPX instrumented for tracing")


@contextmanager
def trace_operation(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
):
    """
    Context manager for tracing an operation.

    Usage:
        with trace_operation("process_claim", {"claim_id": str(claim.id)}):
            result = await process_claim(claim)
    """
    tracer = get_tracer()

    with tracer.start_as_current_span(name, kind=kind) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, str(value))

        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


def trace_function(
    name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
):
    """
    Decorator for tracing a function.

    Usage:
        @trace_function("process_claim")
        async def process_claim(claim_id):
            ...
    """
    def decorator(func: Callable):
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, str(value))

                # Add function arguments as attributes
                span.set_attribute("function.name", func.__name__)

                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = get_tracer()

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, str(value))

                span.set_attribute("function.name", func.__name__)

                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class SpanContextManager:
    """
    Helper for managing spans with additional context.
    """

    @staticmethod
    def add_claim_context(span: Span, claim: Any) -> None:
        """Add claim-related attributes to span"""
        span.set_attribute("claim.id", str(claim.id))
        span.set_attribute("claim.number", claim.claim_number)
        span.set_attribute("claim.type", claim.claim_type.value)
        span.set_attribute("claim.status", claim.status.value)
        span.set_attribute("claim.amount", float(claim.total_amount_claimed))

    @staticmethod
    def add_agent_context(span: Span, agent_name: str, task_type: str) -> None:
        """Add agent-related attributes to span"""
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.task_type", task_type)

    @staticmethod
    def add_llm_context(
        span: Span,
        model: str,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        """Add LLM call attributes to span"""
        span.set_attribute("llm.model", model)
        if prompt_tokens is not None:
            span.set_attribute("llm.prompt_tokens", prompt_tokens)
        if completion_tokens is not None:
            span.set_attribute("llm.completion_tokens", completion_tokens)

    @staticmethod
    def add_error(span: Span, error: Exception, include_traceback: bool = False) -> None:
        """Add error information to span"""
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.record_exception(error, escaped=not include_traceback)
