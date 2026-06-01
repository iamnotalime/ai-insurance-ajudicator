"""
Dependency Injection Container for Insurance Adjudication System
Manages service lifecycle and provides clean dependency resolution
"""

import logging
from typing import TypeVar, Type, Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from contextlib import asynccontextmanager

from .interfaces import (
    IClaimRepository, IPolicyRepository, IAuditLogger,
    IMetricsCollector, ILLMClient, IEncryptor, IRateLimiter,
    ICircuitBreaker, IOrchestrator
)


logger = logging.getLogger(__name__)

T = TypeVar("T")


class ServiceLifetime(Enum):
    """Service lifetime management"""
    SINGLETON = "singleton"      # Single instance for application lifetime
    SCOPED = "scoped"           # New instance per scope (e.g., per request)
    TRANSIENT = "transient"     # New instance every time


@dataclass
class ServiceDescriptor:
    """Describes a registered service"""
    service_type: Type
    implementation: Optional[Type] = None
    factory: Optional[Callable[..., Any]] = None
    instance: Optional[Any] = None
    lifetime: ServiceLifetime = ServiceLifetime.SINGLETON
    dependencies: list = field(default_factory=list)


class ServiceContainer:
    """
    Dependency injection container.

    Features:
    - Singleton, scoped, and transient lifetimes
    - Factory-based and direct registration
    - Async initialization support
    - Scoped contexts for request isolation
    """

    def __init__(self):
        self._services: Dict[Type, ServiceDescriptor] = {}
        self._singletons: Dict[Type, Any] = {}
        self._initialized = False

    def register_singleton(
        self,
        service_type: Type[T],
        implementation: Optional[Type[T]] = None,
        instance: Optional[T] = None,
        factory: Optional[Callable[..., T]] = None,
    ) -> "ServiceContainer":
        """
        Register a singleton service.

        Args:
            service_type: The interface/type to register
            implementation: The concrete implementation class
            instance: A pre-created instance
            factory: A factory function to create the instance
        """
        self._services[service_type] = ServiceDescriptor(
            service_type=service_type,
            implementation=implementation,
            factory=factory,
            instance=instance,
            lifetime=ServiceLifetime.SINGLETON,
        )
        if instance is not None:
            self._singletons[service_type] = instance
        return self

    def register_scoped(
        self,
        service_type: Type[T],
        implementation: Optional[Type[T]] = None,
        factory: Optional[Callable[..., T]] = None,
    ) -> "ServiceContainer":
        """Register a scoped service (new instance per scope)"""
        self._services[service_type] = ServiceDescriptor(
            service_type=service_type,
            implementation=implementation,
            factory=factory,
            lifetime=ServiceLifetime.SCOPED,
        )
        return self

    def register_transient(
        self,
        service_type: Type[T],
        implementation: Optional[Type[T]] = None,
        factory: Optional[Callable[..., T]] = None,
    ) -> "ServiceContainer":
        """Register a transient service (new instance every time)"""
        self._services[service_type] = ServiceDescriptor(
            service_type=service_type,
            implementation=implementation,
            factory=factory,
            lifetime=ServiceLifetime.TRANSIENT,
        )
        return self

    def get(self, service_type: Type[T]) -> T:
        """
        Resolve a service instance.

        For singletons, returns the same instance.
        For transients, creates a new instance.
        For scoped services outside a scope, behaves like transient.
        """
        if service_type not in self._services:
            raise KeyError(f"Service {service_type.__name__} not registered")

        descriptor = self._services[service_type]

        # Return existing singleton
        if descriptor.lifetime == ServiceLifetime.SINGLETON:
            if service_type in self._singletons:
                return self._singletons[service_type]

        # Create instance
        instance = self._create_instance(descriptor)

        # Cache singleton
        if descriptor.lifetime == ServiceLifetime.SINGLETON:
            self._singletons[service_type] = instance

        return instance

    def _create_instance(self, descriptor: ServiceDescriptor) -> Any:
        """Create a service instance"""
        # Pre-created instance
        if descriptor.instance is not None:
            return descriptor.instance

        # Factory function
        if descriptor.factory is not None:
            return descriptor.factory(self)

        # Direct instantiation
        if descriptor.implementation is not None:
            return descriptor.implementation()

        raise ValueError(
            f"Cannot create instance of {descriptor.service_type.__name__}: "
            "no implementation, factory, or instance provided"
        )

    async def initialize(self) -> None:
        """Initialize all singleton services that require async setup"""
        if self._initialized:
            return

        for service_type, descriptor in self._services.items():
            if descriptor.lifetime == ServiceLifetime.SINGLETON:
                instance = self.get(service_type)
                if hasattr(instance, "initialize"):
                    if callable(instance.initialize):
                        result = instance.initialize()
                        if hasattr(result, "__await__"):
                            await result

        self._initialized = True
        logger.info("Service container initialized")

    async def shutdown(self) -> None:
        """Shutdown all services"""
        for service_type, instance in self._singletons.items():
            if hasattr(instance, "shutdown"):
                if callable(instance.shutdown):
                    result = instance.shutdown()
                    if hasattr(result, "__await__"):
                        await result

        self._singletons.clear()
        self._initialized = False
        logger.info("Service container shutdown")

    @asynccontextmanager
    async def scope(self):
        """
        Create a scoped context for request-scoped services.

        Usage:
            async with container.scope() as scoped:
                claim_repo = scoped.get(IClaimRepository)
        """
        scoped_instances: Dict[Type, Any] = {}

        class ScopedContainer:
            def __init__(self, parent: ServiceContainer, scoped: Dict[Type, Any]):
                self._parent = parent
                self._scoped = scoped

            def get(self, service_type: Type[T]) -> T:
                if service_type not in self._parent._services:
                    raise KeyError(f"Service {service_type.__name__} not registered")

                descriptor = self._parent._services[service_type]

                # Singleton - delegate to parent
                if descriptor.lifetime == ServiceLifetime.SINGLETON:
                    return self._parent.get(service_type)

                # Scoped - return from scope cache or create
                if descriptor.lifetime == ServiceLifetime.SCOPED:
                    if service_type not in self._scoped:
                        self._scoped[service_type] = self._parent._create_instance(descriptor)
                    return self._scoped[service_type]

                # Transient - always create new
                return self._parent._create_instance(descriptor)

        scoped = ScopedContainer(self, scoped_instances)

        try:
            yield scoped
        finally:
            # Cleanup scoped instances
            for instance in scoped_instances.values():
                if hasattr(instance, "close"):
                    if callable(instance.close):
                        result = instance.close()
                        if hasattr(result, "__await__"):
                            await result


# Global container instance
_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    """Get the global service container"""
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container


def configure_services(container: ServiceContainer) -> ServiceContainer:
    """
    Configure all services in the container.

    This is the composition root where all dependencies are wired.
    """
    from ..config.settings import settings

    # ==================== Configuration ====================
    container.register_singleton(
        type(settings),
        instance=settings,
    )

    # ==================== Database ====================
    # Repositories are scoped (one per request for transaction isolation)
    def create_claim_repository(c: ServiceContainer):
        from ..database.repository import ClaimRepository
        from ..database.session import get_session_factory
        # Note: In real usage, session is passed from request scope
        return ClaimRepository

    def create_policy_repository(c: ServiceContainer):
        from ..database.repository import PolicyRepository
        return PolicyRepository

    container.register_scoped(IClaimRepository, factory=create_claim_repository)
    container.register_scoped(IPolicyRepository, factory=create_policy_repository)

    # ==================== LLM Client ====================
    def create_llm_client(c: ServiceContainer):
        from ..services.llm_client import create_llm_client
        return create_llm_client()

    container.register_singleton(ILLMClient, factory=create_llm_client)

    # ==================== Observability ====================
    def create_metrics_collector(c: ServiceContainer):
        from ..observability.metrics import MetricsCollector
        return MetricsCollector()

    def create_audit_logger(c: ServiceContainer):
        from ..services.audit import AuditLogger
        return AuditLogger()

    container.register_singleton(IMetricsCollector, factory=create_metrics_collector)
    container.register_singleton(IAuditLogger, factory=create_audit_logger)

    # ==================== Security ====================
    def create_encryptor(c: ServiceContainer):
        from ..utils.encryption import PIIEncryptor
        return PIIEncryptor()

    def create_rate_limiter(c: ServiceContainer):
        from ..middleware.rate_limiting import InMemoryRateLimiter, RateLimitConfig
        return InMemoryRateLimiter(RateLimitConfig())

    container.register_singleton(IEncryptor, factory=create_encryptor)
    container.register_singleton(IRateLimiter, factory=create_rate_limiter)

    # ==================== Circuit Breaker ====================
    def create_circuit_breaker(c: ServiceContainer):
        from ..utils.circuit_breaker import get_llm_circuit_breaker
        return get_llm_circuit_breaker()

    container.register_singleton(ICircuitBreaker, factory=create_circuit_breaker)

    # ==================== Agents & Orchestrator ====================
    def create_orchestrator(c: ServiceContainer):
        from ..agents.orchestrator import AdaptiveOrchestrator
        llm_client = c.get(ILLMClient)
        return AdaptiveOrchestrator(llm_client=llm_client)

    container.register_singleton(IOrchestrator, factory=create_orchestrator)

    logger.info("Services configured in container")
    return container


# FastAPI dependency injection helpers
async def get_claim_repository():
    """FastAPI dependency for claim repository"""
    container = get_container()
    async with container.scope() as scoped:
        yield scoped.get(IClaimRepository)


async def get_policy_repository():
    """FastAPI dependency for policy repository"""
    container = get_container()
    async with container.scope() as scoped:
        yield scoped.get(IPolicyRepository)


def get_llm_client() -> ILLMClient:
    """FastAPI dependency for LLM client"""
    return get_container().get(ILLMClient)


def get_metrics() -> IMetricsCollector:
    """FastAPI dependency for metrics collector"""
    return get_container().get(IMetricsCollector)


def get_audit() -> IAuditLogger:
    """FastAPI dependency for audit logger"""
    return get_container().get(IAuditLogger)


def get_orchestrator() -> IOrchestrator:
    """FastAPI dependency for orchestrator"""
    return get_container().get(IOrchestrator)
