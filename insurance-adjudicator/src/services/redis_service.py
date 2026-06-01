"""
Redis Service for Insurance Adjudication System
Provides centralized Redis connection management, idempotency caching,
and claim queue operations.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from ..config.settings import settings

logger = logging.getLogger(__name__)

# Global Redis client
_redis_client = None
_redis_available = False


async def init_redis() -> bool:
    """Initialize the Redis connection. Returns True if connected."""
    global _redis_client, _redis_available

    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            settings.redis.connection_string,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await _redis_client.ping()
        _redis_available = True
        logger.info(f"Redis connected at {settings.redis.host}:{settings.redis.port}")
        return True
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Using in-memory fallbacks.")
        _redis_available = False
        return False


async def close_redis() -> None:
    """Close the Redis connection"""
    global _redis_client, _redis_available
    if _redis_client:
        try:
            await _redis_client.close()
        except Exception:
            pass
        _redis_client = None
        _redis_available = False
        logger.info("Redis connection closed")


def get_redis_client():
    """Get the global Redis client (may be None if not connected)"""
    return _redis_client


def is_redis_available() -> bool:
    """Check if Redis is currently available"""
    return _redis_available


# ============================================================
# Idempotency Cache
# ============================================================

# In-memory fallback for when Redis is unavailable
_idempotency_memory_cache: dict[str, Any] = {}

IDEMPOTENCY_PREFIX = "idempotency:"
IDEMPOTENCY_TTL = 3600 * 24  # 24 hours


async def get_idempotency_result(key: str) -> Optional[str]:
    """Get a cached idempotency result"""
    if _redis_available and _redis_client:
        try:
            result = await _redis_client.get(f"{IDEMPOTENCY_PREFIX}{key}")
            return result
        except Exception as e:
            logger.warning(f"Redis idempotency read failed: {e}")

    # Fallback to memory
    return _idempotency_memory_cache.get(key)


async def set_idempotency_result(key: str, value: str) -> None:
    """Cache an idempotency result"""
    if _redis_available and _redis_client:
        try:
            await _redis_client.setex(
                f"{IDEMPOTENCY_PREFIX}{key}",
                IDEMPOTENCY_TTL,
                value,
            )
            return
        except Exception as e:
            logger.warning(f"Redis idempotency write failed: {e}")

    # Fallback to memory
    _idempotency_memory_cache[key] = value


# ============================================================
# Claim Queue Operations (used by API to enqueue, worker to dequeue)
# ============================================================

QUEUE_NAME = "claims:pending"


async def enqueue_claim(claim_id: UUID) -> bool:
    """Push a claim ID onto the Redis processing queue"""
    if not _redis_available or not _redis_client:
        return False

    try:
        data = json.dumps({
            "claim_id": str(claim_id),
            "attempt": 1,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        })
        await _redis_client.lpush(QUEUE_NAME, data)
        logger.info(f"Claim {claim_id} enqueued to Redis")
        return True
    except Exception as e:
        logger.warning(f"Failed to enqueue claim {claim_id}: {e}")
        return False


# ============================================================
# Health Check
# ============================================================

async def redis_health_check() -> dict:
    """Check Redis connectivity"""
    if not _redis_available or not _redis_client:
        return {"status": "unavailable", "redis": "disconnected"}

    try:
        await _redis_client.ping()
        info = await _redis_client.info("memory")
        return {
            "status": "healthy",
            "redis": "connected",
            "used_memory_human": info.get("used_memory_human", "unknown"),
        }
    except Exception as e:
        return {"status": "unhealthy", "redis": "error", "error": str(e)}
