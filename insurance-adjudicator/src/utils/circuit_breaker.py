"""
Circuit Breaker Pattern Implementation
Provides resilience for external service calls (LLM, APIs, etc.)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any, Optional, TypeVar, Generic
from functools import wraps


logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failures exceeded threshold, rejecting calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5  # Failures before opening circuit
    success_threshold: int = 3  # Successes needed to close circuit from half-open
    timeout: float = 30.0  # Seconds before transitioning from open to half-open
    half_open_max_calls: int = 3  # Max concurrent calls in half-open state

    # Specific error types that should trigger the circuit breaker
    # None means all exceptions trigger it
    exception_types: tuple = (Exception,)

    # Errors that should NOT trigger the circuit breaker
    excluded_exceptions: tuple = ()


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""

    def __init__(self, circuit_name: str, retry_after: float):
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker '{circuit_name}' is open. Retry after {retry_after:.1f} seconds."
        )


class CircuitBreaker:
    """
    Circuit breaker implementation for resilient service calls.

    Usage:
        breaker = CircuitBreaker("llm_api")

        @breaker
        async def call_llm(prompt):
            return await llm_client.generate(prompt)

    Or manually:
        async with breaker:
            result = await call_external_service()
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        fallback: Optional[Callable[..., Any]] = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.fallback = fallback

        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._last_state_change = time.time()
        self._lock = asyncio.Lock()
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state"""
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        """Circuit breaker statistics"""
        return self._stats

    def _should_trigger(self, exception: Exception) -> bool:
        """Check if exception should trigger circuit breaker"""
        if self.config.excluded_exceptions:
            if isinstance(exception, self.config.excluded_exceptions):
                return False

        return isinstance(exception, self.config.exception_types)

    async def _check_state(self) -> bool:
        """
        Check if call should be allowed based on circuit state.
        Returns True if call is allowed.
        """
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Check if timeout has passed
                elapsed = time.time() - self._last_state_change
                if elapsed >= self.config.timeout:
                    logger.info(
                        f"Circuit breaker '{self.name}' transitioning to half-open"
                    )
                    self._state = CircuitState.HALF_OPEN
                    self._last_state_change = time.time()
                    self._half_open_calls = 0
                    return True

                # Circuit is still open
                return False

            if self._state == CircuitState.HALF_OPEN:
                # Allow limited calls in half-open state
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            return False

    async def _record_success(self) -> None:
        """Record a successful call"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.successful_calls += 1
            self._stats.last_success_time = time.time()
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                if self._stats.consecutive_successes >= self.config.success_threshold:
                    logger.info(
                        f"Circuit breaker '{self.name}' closing after recovery"
                    )
                    self._state = CircuitState.CLOSED   
                    self._last_state_change = time.time()
                    self._stats.consecutive_successes = 0

    async def _record_failure(self, exception: Exception) -> None:
        """Record a failed call"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.failed_calls += 1
            self._stats.last_failure_time = time.time()
            self._stats.consecutive_successes = 0
            self._stats.consecutive_failures += 1

            logger.warning(
                f"Circuit breaker '{self.name}' recorded failure: {exception}"
            )

            if self._state == CircuitState.CLOSED:
                if self._stats.consecutive_failures >= self.config.failure_threshold:
                    logger.error(
                        f"Circuit breaker '{self.name}' opening due to "
                        f"{self._stats.consecutive_failures} consecutive failures"
                    )
                    self._state = CircuitState.OPEN
                    self._last_state_change = time.time()

            elif self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open state opens the circuit
                logger.warning(
                    f"Circuit breaker '{self.name}' reopening from half-open"
                )
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()
                self._stats.consecutive_failures = 0

    async def _record_rejection(self) -> None:
        """Record a rejected call"""
        async with self._lock:
            self._stats.rejected_calls += 1

    def _get_retry_after(self) -> float:
        """Get time until circuit breaker might allow calls"""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change
            return max(0, self.config.timeout - elapsed)
        return 0

    async def __aenter__(self):
        """Context manager entry"""
        allowed = await self._check_state()
        if not allowed:
            await self._record_rejection()
            raise CircuitBreakerError(self.name, self._get_retry_after())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if exc_type is None:
            await self._record_success()
        elif self._should_trigger(exc_val):
            await self._record_failure(exc_val)
        # Don't suppress the exception
        return False

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator for circuit breaker"""

        @wraps(func)
        async def wrapper(*args, **kwargs):
            allowed = await self._check_state()

            if not allowed:
                await self._record_rejection()

                # Try fallback if available
                if self.fallback:
                    logger.info(f"Using fallback for '{self.name}'")
                    return await self.fallback(*args, **kwargs)

                raise CircuitBreakerError(self.name, self._get_retry_after())

            try:
                result = await func(*args, **kwargs)
                await self._record_success()
                return result
            except Exception as e:
                if self._should_trigger(e):
                    await self._record_failure(e)
                raise

        return wrapper

    def reset(self) -> None:
        """Manually reset the circuit breaker"""
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._last_state_change = time.time()
        logger.info(f"Circuit breaker '{self.name}' manually reset")


# Registry for circuit breakers
class CircuitBreakerRegistry:
    """Registry for managing circuit breakers"""

    _instance: Optional["CircuitBreakerRegistry"] = None
    _breakers: dict[str, CircuitBreaker] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._breakers = {}
        return cls._instance

    def register(self, breaker: CircuitBreaker) -> None:
        """Register a circuit breaker"""
        self._breakers[breaker.name] = breaker

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get a circuit breaker by name"""
        return self._breakers.get(name)

    def get_or_create(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        fallback: Optional[Callable] = None,
    ) -> CircuitBreaker:
        """Get existing circuit breaker or create new one"""
        if name not in self._breakers:
            breaker = CircuitBreaker(name, config, fallback)
            self._breakers[name] = breaker
        return self._breakers[name]

    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all circuit breakers"""
        return {
            name: {
                "state": breaker.state.value,
                "total_calls": breaker.stats.total_calls,
                "successful_calls": breaker.stats.successful_calls,
                "failed_calls": breaker.stats.failed_calls,
                "rejected_calls": breaker.stats.rejected_calls,
                "consecutive_failures": breaker.stats.consecutive_failures,
            }
            for name, breaker in self._breakers.items()
        }


# Global registry
circuit_breaker_registry = CircuitBreakerRegistry()


# Pre-configured circuit breakers for common services
def get_llm_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for LLM API calls"""
    return circuit_breaker_registry.get_or_create(
        "llm_api",
        CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout=60.0,  # Wait 60 seconds before retrying
            half_open_max_calls=1,
        ),
    )


def get_database_circuit_breaker() -> CircuitBreaker:
    """Get circuit breaker for database calls"""
    return circuit_breaker_registry.get_or_create(
        "database",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=3,
            timeout=30.0,
            half_open_max_calls=2,
        ),
    )
