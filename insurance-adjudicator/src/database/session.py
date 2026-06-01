"""
Database Session Management for Insurance Adjudication System
Provides async database connections with connection pooling
"""

import logging
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    AsyncEngine,
    async_sessionmaker
)
from sqlalchemy.pool import NullPool

from ..config.settings import settings
from .models import Base


logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    """Get the async database URL"""
    return settings.database.async_connection_string


def get_engine() -> AsyncEngine:
    """Get or create the database engine"""
    global _engine

    if _engine is None:
        database_url = get_database_url()

        # Configure pool based on environment
        if settings.is_production:
            pool_kwargs = {
                "pool_size": settings.database.pool_size,
                "max_overflow": settings.database.max_overflow,
                "pool_pre_ping": True,  # Verify connections before use
                "pool_recycle": 3600,  # Recycle connections after 1 hour
            }
        else:
            # Use NullPool for development to avoid connection issues
            pool_kwargs = {"poolclass": NullPool}

        _engine = create_async_engine(
            database_url,
            echo=settings.is_development,  # Log SQL in development
            **pool_kwargs
        )

        logger.info(f"Database engine created for {settings.database.host}:{settings.database.port}")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory"""
    global _session_factory

    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Prevent detached instance errors
            autocommit=False,
            autoflush=False,
        )

    return _session_factory


async def init_db() -> None:
    """Initialize database connectivity and optionally create tables."""
    engine = get_engine()

    if settings.database.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized")
        return

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connectivity verified; schema creation disabled")


async def close_db() -> None:
    """Close database connections"""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connections closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection for database sessions.
    Use with FastAPI's Depends().
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def DatabaseSession() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.
    Use for background tasks or non-FastAPI code.
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class DatabaseHealthCheck:
    """Health check for database connectivity"""

    @staticmethod
    async def check() -> dict:
        """Check database connectivity"""
        try:
            async with DatabaseSession() as session:
                result = await session.execute(text("SELECT 1"))
                result.scalar()
                return {"status": "healthy", "database": "connected"}
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {"status": "unhealthy", "database": "disconnected", "error": str(e)}
