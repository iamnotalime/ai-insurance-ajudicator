"""
Prometheus Metrics for Insurance Adjudication System
Provides application metrics for monitoring and alerting
"""

import logging
import time
from functools import wraps
from typing import Optional, Callable, Any

from prometheus_client import (
    Counter, Histogram, Gauge, Summary, Info,
    REGISTRY, generate_latest, CONTENT_TYPE_LATEST,
    CollectorRegistry
)
from prometheus_client.multiprocess import MultiProcessCollector

from ..config.settings import settings


logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Centralized metrics collection for the application.
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or REGISTRY

        # Application info
        self.app_info = Info(
            "insurance_adjudicator",
            "Application information",
            registry=self.registry,
        )
        self.app_info.info({
            "version": settings.version,
            "environment": settings.environment.value,
        })

        # ==================== Request Metrics ====================

        self.http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status_code"],
            registry=self.registry,
        )

        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )

        self.http_requests_in_progress = Gauge(
            "http_requests_in_progress",
            "HTTP requests currently in progress",
            ["method", "endpoint"],
            registry=self.registry,
        )

        # ==================== Claim Metrics ====================

        self.claims_submitted_total = Counter(
            "claims_submitted_total",
            "Total claims submitted",
            ["claim_type"],
            registry=self.registry,
        )

        self.claims_processed_total = Counter(
            "claims_processed_total",
            "Total claims processed",
            ["claim_type", "decision", "required_human_review"],
            registry=self.registry,
        )

        self.claim_processing_duration_seconds = Histogram(
            "claim_processing_duration_seconds",
            "Claim processing duration in seconds",
            ["claim_type"],
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
            registry=self.registry,
        )

        self.claim_amount_dollars = Histogram(
            "claim_amount_dollars",
            "Claimed amount distribution in dollars",
            ["claim_type"],
            buckets=(100, 500, 1000, 5000, 10000, 50000, 100000, 500000),
            registry=self.registry,
        )

        self.claims_pending = Gauge(
            "claims_pending",
            "Claims currently pending processing",
            ["priority"],
            registry=self.registry,
        )

        # ==================== Agent Metrics ====================

        self.agent_executions_total = Counter(
            "agent_executions_total",
            "Total agent task executions",
            ["agent_name", "task_type", "status"],
            registry=self.registry,
        )

        self.agent_execution_duration_seconds = Histogram(
            "agent_execution_duration_seconds",
            "Agent execution duration in seconds",
            ["agent_name", "task_type"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
            registry=self.registry,
        )

        self.agent_confidence_score = Summary(
            "agent_confidence_score",
            "Agent decision confidence scores",
            ["agent_name"],
            registry=self.registry,
        )

        # ==================== LLM Metrics ====================

        self.llm_requests_total = Counter(
            "llm_requests_total",
            "Total LLM API requests",
            ["model", "status"],
            registry=self.registry,
        )

        self.llm_request_duration_seconds = Histogram(
            "llm_request_duration_seconds",
            "LLM request duration in seconds",
            ["model"],
            buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
            registry=self.registry,
        )

        self.llm_tokens_used = Counter(
            "llm_tokens_used_total",
            "Total LLM tokens used",
            ["model", "token_type"],  # token_type: prompt, completion
            registry=self.registry,
        )

        # ==================== Circuit Breaker Metrics ====================

        self.circuit_breaker_state = Gauge(
            "circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ["circuit_name"],
            registry=self.registry,
        )

        self.circuit_breaker_calls_total = Counter(
            "circuit_breaker_calls_total",
            "Total circuit breaker calls",
            ["circuit_name", "result"],  # result: success, failure, rejected
            registry=self.registry,
        )

        # ==================== Database Metrics ====================

        self.db_queries_total = Counter(
            "db_queries_total",
            "Total database queries",
            ["operation", "table"],
            registry=self.registry,
        )

        self.db_query_duration_seconds = Histogram(
            "db_query_duration_seconds",
            "Database query duration in seconds",
            ["operation"],
            buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=self.registry,
        )

        self.db_connection_pool_size = Gauge(
            "db_connection_pool_size",
            "Database connection pool size",
            ["state"],  # state: active, idle, overflow
            registry=self.registry,
        )

        # ==================== Fraud Detection Metrics ====================

        self.fraud_indicators_detected_total = Counter(
            "fraud_indicators_detected_total",
            "Total fraud indicators detected",
            ["indicator_type", "severity"],
            registry=self.registry,
        )

        self.fraud_risk_score = Summary(
            "fraud_risk_score",
            "Fraud risk score distribution",
            registry=self.registry,
        )

        # ==================== Rate Limiting Metrics ====================

        self.rate_limit_hits_total = Counter(
            "rate_limit_hits_total",
            "Total rate limit hits",
            ["endpoint", "identifier_type"],
            registry=self.registry,
        )

    # Helper methods for recording metrics

    def record_http_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration: float,
    ) -> None:
        """Record an HTTP request"""
        self.http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code=str(status_code),
        ).inc()

        self.http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

    def record_claim_submitted(self, claim_type: str, amount: float) -> None:
        """Record a submitted claim"""
        self.claims_submitted_total.labels(claim_type=claim_type).inc()
        self.claim_amount_dollars.labels(claim_type=claim_type).observe(amount)

    def record_claim_processed(
        self,
        claim_type: str,
        decision: str,
        required_human_review: bool,
        duration: float,
    ) -> None:
        """Record a processed claim"""
        self.claims_processed_total.labels(
            claim_type=claim_type,
            decision=decision,
            required_human_review=str(required_human_review).lower(),
        ).inc()

        self.claim_processing_duration_seconds.labels(
            claim_type=claim_type,
        ).observe(duration)

    def record_agent_execution(
        self,
        agent_name: str,
        task_type: str,
        status: str,
        duration: float,
        confidence: Optional[float] = None,
    ) -> None:
        """Record an agent execution"""
        self.agent_executions_total.labels(
            agent_name=agent_name,
            task_type=task_type,
            status=status,
        ).inc()

        self.agent_execution_duration_seconds.labels(
            agent_name=agent_name,
            task_type=task_type,
        ).observe(duration)

        if confidence is not None:
            self.agent_confidence_score.labels(
                agent_name=agent_name,
            ).observe(confidence)

    def record_llm_request(
        self,
        model: str,
        status: str,
        duration: float,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        """Record an LLM request"""
        self.llm_requests_total.labels(model=model, status=status).inc()
        self.llm_request_duration_seconds.labels(model=model).observe(duration)

        if prompt_tokens is not None:
            self.llm_tokens_used.labels(model=model, token_type="prompt").inc(prompt_tokens)
        if completion_tokens is not None:
            self.llm_tokens_used.labels(model=model, token_type="completion").inc(completion_tokens)

    def record_fraud_indicator(self, indicator_type: str, severity: str) -> None:
        """Record a fraud indicator detection"""
        self.fraud_indicators_detected_total.labels(
            indicator_type=indicator_type,
            severity=severity,
        ).inc()

    def record_fraud_risk_score(self, score: float) -> None:
        """Record a fraud risk score"""
        self.fraud_risk_score.observe(score)

    def update_circuit_breaker_state(self, circuit_name: str, state: int) -> None:
        """Update circuit breaker state (0=closed, 1=half_open, 2=open)"""
        self.circuit_breaker_state.labels(circuit_name=circuit_name).set(state)

    def record_circuit_breaker_call(self, circuit_name: str, result: str) -> None:
        """Record a circuit breaker call result"""
        self.circuit_breaker_calls_total.labels(
            circuit_name=circuit_name,
            result=result,
        ).inc()


# Global metrics collector
_metrics_collector: Optional[MetricsCollector] = None


def setup_metrics() -> MetricsCollector:
    """Initialize metrics collection"""
    global _metrics_collector
    _metrics_collector = MetricsCollector()
    logger.info("Prometheus metrics initialized")
    return _metrics_collector


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def track_time(
    histogram: Histogram,
    labels: Optional[dict] = None,
) -> Callable:
    """
    Decorator to track function execution time.

    Usage:
        @track_time(metrics.claim_processing_duration_seconds, {"claim_type": "auto"})
        async def process_claim():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.time() - start
                if labels:
                    histogram.labels(**labels).observe(duration)
                else:
                    histogram.observe(duration)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.time() - start
                if labels:
                    histogram.labels(**labels).observe(duration)
                else:
                    histogram.observe(duration)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def get_metrics_endpoint() -> tuple[bytes, str]:
    """
    Get metrics in Prometheus format.

    Returns:
        Tuple of (metrics_bytes, content_type)
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
