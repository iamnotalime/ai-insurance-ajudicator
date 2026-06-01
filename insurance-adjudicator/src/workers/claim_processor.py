"""
Background Claim Processor Worker
Polls Redis queue for pending claims and processes them with the AI agent pipeline.
Designed to run as a separate process alongside the API server.
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config.settings import settings
from src.database.session import init_db, close_db, DatabaseSession
from src.database.repository import ClaimRepository
from src.agents.orchestrator import AdaptiveOrchestrator
from src.agents.base import agent_registry
from src.agents.specialized import (
    DocumentExtractionAgent, PolicyAnalysisAgent,
    FraudDetectionAgent, DecisionAgent,
)
from src.services.llm_client import create_llm_client
from src.services.audit import get_audit_logger
from src.observability.logging import setup_structured_logging
from src.observability.metrics import setup_metrics, get_metrics_collector
from src.models.claim import ClaimStatus


logger = logging.getLogger(__name__)

# Graceful shutdown
_shutdown_event = asyncio.Event()

QUEUE_NAME = "claims:pending"
PROCESSING_QUEUE = "claims:processing"
DEAD_LETTER_QUEUE = "claims:dead_letter"
MAX_RETRIES = 3
POLL_INTERVAL = 2  # seconds


async def get_redis():
    """Create a Redis connection"""
    import redis.asyncio as aioredis
    return aioredis.from_url(
        settings.redis.connection_string,
        decode_responses=True,
        socket_connect_timeout=5,
    )


async def process_claim(claim_id: UUID) -> bool:
    """Process a single claim through the AI agent pipeline"""
    metrics = get_metrics_collector()
    audit_logger = get_audit_logger()

    async with DatabaseSession() as session:
        claim_repo = ClaimRepository(session)
        claim_db = await claim_repo.get_by_id_with_relations(claim_id)

        if not claim_db:
            logger.error(f"Claim {claim_id} not found")
            return False

        claim = claim_repo.db_to_pydantic(claim_db)

    if claim.status not in [ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW]:
        logger.info(f"Claim {claim_id} already processed (status: {claim.status})")
        return True

    try:
        start_time = datetime.now(timezone.utc)

        llm_client = create_llm_client()
        orchestrator = AdaptiveOrchestrator(llm_client=llm_client)
        result = await orchestrator.process_claim(claim)

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        if result.decision:
            claim.decision = result.decision
            async with DatabaseSession() as session:
                claim_repo = ClaimRepository(session)
                await claim_repo.update_decision(claim_id, result.decision, claim.status)

        metrics.record_claim_processed(
            claim_type=claim.claim_type.value,
            decision=result.decision.decision if result.decision else "error",
            required_human_review=result.requires_human_review,
            duration=duration,
        )

        if result.decision:
            await audit_logger.log_decision_made(
                claim_id=claim_id,
                decision=result.decision.decision,
                confidence=result.decision.confidence_score,
                requires_review=result.requires_human_review,
                agent_name="worker_decision_agent",
            )

        logger.info(
            f"Claim {claim.claim_number} processed: {result.status} "
            f"(duration: {duration:.2f}s)"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to process claim {claim_id}: {e}", exc_info=True)

        async with DatabaseSession() as session:
            claim_repo = ClaimRepository(session)
            await claim_repo.update_status(claim_id, ClaimStatus.UNDER_REVIEW)

        return False


async def worker_loop(redis_client):
    """Main worker loop: poll Redis queue and process claims"""
    logger.info("Worker loop started, polling for claims...")

    while not _shutdown_event.is_set():
        try:
            # BRPOPLPUSH: atomically move from pending to processing queue
            result = await redis_client.brpoplpush(
                QUEUE_NAME, PROCESSING_QUEUE, timeout=POLL_INTERVAL
            )

            if result is None:
                continue

            claim_data = json.loads(result)
            claim_id = UUID(claim_data["claim_id"])
            attempt = claim_data.get("attempt", 1)

            logger.info(f"Processing claim {claim_id} (attempt {attempt}/{MAX_RETRIES})")

            success = await process_claim(claim_id)

            if success:
                # Remove from processing queue
                await redis_client.lrem(PROCESSING_QUEUE, 1, result)
                logger.info(f"Claim {claim_id} completed successfully")
            else:
                # Remove from processing queue
                await redis_client.lrem(PROCESSING_QUEUE, 1, result)

                if attempt < MAX_RETRIES:
                    # Re-queue with incremented attempt and backoff delay
                    delay = 2 ** attempt
                    logger.warning(
                        f"Claim {claim_id} failed, retrying in {delay}s "
                        f"(attempt {attempt}/{MAX_RETRIES})"
                    )
                    await asyncio.sleep(delay)
                    retry_data = json.dumps({
                        "claim_id": str(claim_id),
                        "attempt": attempt + 1,
                        "last_error_at": datetime.now(timezone.utc).isoformat(),
                    })
                    await redis_client.lpush(QUEUE_NAME, retry_data)
                else:
                    # Send to dead-letter queue
                    dead_letter = json.dumps({
                        "claim_id": str(claim_id),
                        "attempts": attempt,
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    await redis_client.lpush(DEAD_LETTER_QUEUE, dead_letter)
                    logger.error(
                        f"Claim {claim_id} failed after {MAX_RETRIES} attempts, "
                        f"moved to dead-letter queue"
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    logger.info("Worker loop stopped")


async def recover_processing_queue(redis_client):
    """Recover claims stuck in the processing queue (from crashed workers)"""
    stuck = await redis_client.lrange(PROCESSING_QUEUE, 0, -1)
    if stuck:
        logger.warning(f"Recovering {len(stuck)} stuck claims from processing queue")
        for item in stuck:
            await redis_client.lpush(QUEUE_NAME, item)
        await redis_client.delete(PROCESSING_QUEUE)


async def main():
    """Main entry point for the worker process"""
    setup_structured_logging()
    setup_metrics()

    logger.info("Starting claim processor worker...")
    logger.info(f"Environment: {settings.environment.value}")
    logger.info(f"Database: {settings.database.host}:{settings.database.port}")
    logger.info(f"Redis: {settings.redis.host}:{settings.redis.port}")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Initialize agents
    llm_client = create_llm_client()
    agents = [
        DocumentExtractionAgent(llm_client=llm_client),
        PolicyAnalysisAgent(llm_client=llm_client),
        FraudDetectionAgent(llm_client=llm_client),
        DecisionAgent(llm_client=llm_client),
    ]
    for agent in agents:
        agent_registry.register(agent)
    await agent_registry.initialize_all()
    logger.info("Agents initialized")

    # Connect to Redis
    try:
        redis_client = await get_redis()
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Cannot connect to Redis: {e}")
        logger.error("Worker requires Redis. Exiting.")
        await close_db()
        sys.exit(1)

    # Recover stuck claims from previous crashes
    await recover_processing_queue(redis_client)

    # Run worker
    try:
        await worker_loop(redis_client)
    finally:
        logger.info("Shutting down worker...")
        await redis_client.close()
        await agent_registry.shutdown_all()
        await close_db()
        logger.info("Worker shutdown complete")


def handle_signal(sig, frame):
    """Handle OS signals for graceful shutdown"""
    logger.info(f"Received signal {sig}, initiating shutdown...")
    _shutdown_event.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    asyncio.run(main())
