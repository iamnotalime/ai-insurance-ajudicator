"""
Rate Limiting Middleware for Insurance Adjudication System
Implements sliding window rate limiting with Redis backend
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Callable, Awaitable
from datetime import datetime

from fastapi import Request, Response, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..config.settings import settings


logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration"""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    requests_per_day: int = 10000
    burst_limit: int = 10  # Max requests in 1 second

    # Endpoint-specific limits (path prefix -> requests per minute)
    endpoint_limits: Dict[str, int] = None

    def __post_init__(self):
        if self.endpoint_limits is None:
            self.endpoint_limits = {
                "/api/v1/claims/batch": 10,  # Batch processing is expensive
                "/api/v1/claims/adjudicate": 30,  # Adjudication is resource-intensive
            }


class InMemoryRateLimiter:
    """
    In-memory rate limiter using sliding window algorithm.
    For production, use Redis-backed implementation.
    """

    def __init__(self, config: RateLimitConfig):
        self.config = config
        self._windows: Dict[str, Dict[str, list]] = {}
        self._lock = asyncio.Lock()

    async def is_allowed(
        self,
        identifier: str,
        endpoint: Optional[str] = None
    ) -> tuple[bool, Dict[str, int]]:
        """
        Check if request is allowed under rate limits.

        Returns:
            Tuple of (is_allowed, rate_limit_headers)
        """
        now = time.time()

        async with self._lock:
            if identifier not in self._windows:
                self._windows[identifier] = {
                    "minute": [],
                    "hour": [],
                    "day": [],
                    "second": [],
                }

            windows = self._windows[identifier]

            # Clean old entries
            minute_ago = now - 60
            hour_ago = now - 3600
            day_ago = now - 86400
            second_ago = now - 1

            windows["minute"] = [t for t in windows["minute"] if t > minute_ago]
            windows["hour"] = [t for t in windows["hour"] if t > hour_ago]
            windows["day"] = [t for t in windows["day"] if t > day_ago]
            windows["second"] = [t for t in windows["second"] if t > second_ago]

            # Get endpoint-specific limit
            minute_limit = self.config.requests_per_minute
            if endpoint:
                for prefix, limit in self.config.endpoint_limits.items():
                    if endpoint.startswith(prefix):
                        minute_limit = limit
                        break

            # Check limits
            minute_count = len(windows["minute"])
            hour_count = len(windows["hour"])
            day_count = len(windows["day"])
            second_count = len(windows["second"])

            is_allowed = (
                minute_count < minute_limit and
                hour_count < self.config.requests_per_hour and
                day_count < self.config.requests_per_day and
                second_count < self.config.burst_limit
            )

            if is_allowed:
                # Record the request
                windows["minute"].append(now)
                windows["hour"].append(now)
                windows["day"].append(now)
                windows["second"].append(now)

            # Calculate headers
            remaining = minute_limit - minute_count - (1 if is_allowed else 0)
            reset_time = int(now + 60 - (now % 60))

            headers = {
                "X-RateLimit-Limit": minute_limit,
                "X-RateLimit-Remaining": max(0, remaining),
                "X-RateLimit-Reset": reset_time,
            }

            return is_allowed, headers

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for an identifier"""
        async with self._lock:
            if identifier in self._windows:
                del self._windows[identifier]


class RedisRateLimiter:
    """
    Redis-backed rate limiter for production use.
    Provides distributed rate limiting across multiple instances.
    Falls back to InMemoryRateLimiter when Redis is unavailable.
    """

    def __init__(self, config: RateLimitConfig, redis_url: Optional[str] = None):
        self.config = config
        self._redis_url = redis_url
        self._redis = None
        self._redis_available = False
        self._fallback = InMemoryRateLimiter(config)

    async def connect(self) -> None:
        """Attempt to connect to Redis"""
        if not self._redis_url:
            logger.info("No Redis URL configured, using in-memory rate limiter")
            return

        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self._redis.ping()
            self._redis_available = True
            logger.info("Redis rate limiter connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed, using in-memory fallback: {e}")
            self._redis_available = False

    async def close(self) -> None:
        """Close Redis connection"""
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None
            self._redis_available = False

    async def is_allowed(
        self,
        identifier: str,
        endpoint: Optional[str] = None
    ) -> tuple[bool, Dict[str, int]]:
        """Check if request is allowed under rate limits"""
        if not self._redis_available:
            return await self._fallback.is_allowed(identifier, endpoint)

        try:
            now = int(time.time())
            minute_key = f"ratelimit:{identifier}:minute:{now // 60}"
            hour_key = f"ratelimit:{identifier}:hour:{now // 3600}"

            # Get endpoint-specific limit
            minute_limit = self.config.requests_per_minute
            if endpoint:
                for prefix, limit in self.config.endpoint_limits.items():
                    if endpoint.startswith(prefix):
                        minute_limit = limit
                        break

            pipe = self._redis.pipeline()
            pipe.incr(minute_key)
            pipe.expire(minute_key, 60)
            pipe.incr(hour_key)
            pipe.expire(hour_key, 3600)

            results = await pipe.execute()
            minute_count = results[0]
            hour_count = results[2]

            is_allowed = (
                minute_count <= minute_limit and
                hour_count <= self.config.requests_per_hour
            )

            headers = {
                "X-RateLimit-Limit": minute_limit,
                "X-RateLimit-Remaining": max(0, minute_limit - minute_count),
                "X-RateLimit-Reset": (now // 60 + 1) * 60,
            }

            return is_allowed, headers

        except Exception as e:
            logger.warning(f"Redis rate limit check failed, falling back to in-memory: {e}")
            self._redis_available = False
            return await self._fallback.is_allowed(identifier, endpoint)

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for an identifier"""
        if self._redis_available:
            try:
                keys = await self._redis.keys(f"ratelimit:{identifier}:*")
                if keys:
                    await self._redis.delete(*keys)
                return
            except Exception:
                pass
        await self._fallback.reset(identifier)


# Global rate limiter instances
RateLimiter = InMemoryRateLimiter(RateLimitConfig())
_redis_rate_limiter: Optional[RedisRateLimiter] = None


async def get_rate_limiter() -> RedisRateLimiter:
    """Get or create the Redis-backed rate limiter singleton"""
    global _redis_rate_limiter
    if _redis_rate_limiter is None:
        config = RateLimitConfig(
            requests_per_minute=settings.rate_limit.requests_per_minute,
            requests_per_hour=settings.rate_limit.requests_per_hour,
            requests_per_day=settings.rate_limit.requests_per_day,
            burst_limit=settings.rate_limit.burst_limit,
        )
        _redis_rate_limiter = RedisRateLimiter(
            config=config,
            redis_url=settings.redis.connection_string,
        )
        await _redis_rate_limiter.connect()
    return _redis_rate_limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting.
    Applies rate limits based on API key or IP address.
    """

    def __init__(
        self,
        app: ASGIApp,
        rate_limiter: Optional[InMemoryRateLimiter] = None,
        skip_paths: Optional[list] = None,
    ):
        super().__init__(app)
        self.rate_limiter = rate_limiter or RateLimiter
        self.skip_paths = skip_paths or ["/health", "/ready", "/metrics"]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with rate limiting"""

        # Skip rate limiting for health checks and metrics
        if any(request.url.path.startswith(p) for p in self.skip_paths):
            return await call_next(request)

        # Get identifier (API key or IP)
        identifier = self._get_identifier(request)

        # Check rate limit
        is_allowed, headers = await self.rate_limiter.is_allowed(
            identifier,
            endpoint=request.url.path
        )

        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {identifier} on {request.url.path}"
            )
            response = Response(
                content='{"detail": "Rate limit exceeded. Please try again later."}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
            )
            # Add rate limit headers
            for key, value in headers.items():
                response.headers[key] = str(value)
            response.headers["Retry-After"] = str(headers["X-RateLimit-Reset"] - int(time.time()))
            return response

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        for key, value in headers.items():
            response.headers[key] = str(value)

        return response

    def _get_identifier(self, request: Request) -> str:
        """Extract rate limit identifier from request"""
        # Try API key first
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
            return f"api_key:{api_key[:16]}..."  # Truncate for privacy

        # Fall back to IP address
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take first IP from forwarded chain
            ip = forwarded_for.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"

        return f"ip:{ip}"
