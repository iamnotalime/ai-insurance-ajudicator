"""
Insurance Adjudicator System Configuration
Enterprise-grade settings with validation, feature flags, and environment support
"""

import base64
import logging
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import quote_plus

from ..core.exceptions import ConfigurationError


logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_csv(name: str, default: str = "") -> List[str]:
    """Parse a comma-separated environment variable."""
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _looks_like_placeholder(value: Optional[str]) -> bool:
    """Detect development placeholders that should never reach production."""
    if not value:
        return True
    normalized = value.strip().lower()
    placeholders = {
        "change-me",
        "change-me-in-production",
        "changeme",
        "secret",
        "dev-secret",
        "dev-jwt-secret-change-in-production",
        "postgres",
        "password",
        "admin",
        "test",
    }
    return normalized in placeholders or normalized.startswith("dev-")


class Environment(Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


# ==================== Feature Flags ====================

@dataclass
class FeatureFlags:
    """Feature flags for gradual rollout and A/B testing"""

    # Core features
    enable_fraud_detection: bool = field(
        default_factory=lambda: os.getenv("FF_FRAUD_DETECTION", "true").lower() == "true"
    )
    enable_document_extraction: bool = field(
        default_factory=lambda: os.getenv("FF_DOCUMENT_EXTRACTION", "true").lower() == "true"
    )
    enable_auto_adjudication: bool = field(
        default_factory=lambda: os.getenv("FF_AUTO_ADJUDICATION", "true").lower() == "true"
    )

    # Experimental features
    enable_parallel_agents: bool = field(
        default_factory=lambda: os.getenv("FF_PARALLEL_AGENTS", "true").lower() == "true"
    )
    enable_adaptive_orchestration: bool = field(
        default_factory=lambda: os.getenv("FF_ADAPTIVE_ORCHESTRATION", "true").lower() == "true"
    )

    # Security features
    enable_pii_encryption: bool = field(
        default_factory=lambda: os.getenv("FF_PII_ENCRYPTION", "true").lower() == "true"
    )
    enable_audit_logging: bool = field(
        default_factory=lambda: os.getenv("FF_AUDIT_LOGGING", "true").lower() == "true"
    )
    enable_rate_limiting: bool = field(
        default_factory=lambda: os.getenv("FF_RATE_LIMITING", "true").lower() == "true"
    )

    # Observability
    enable_metrics: bool = field(
        default_factory=lambda: os.getenv("FF_METRICS", "true").lower() == "true"
    )
    enable_tracing: bool = field(
        default_factory=lambda: os.getenv("FF_TRACING", "true").lower() == "true"
    )

    # Database
    use_database: bool = field(
        default_factory=lambda: os.getenv("FF_USE_DATABASE", "false").lower() == "true"
    )

    def is_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled by name"""
        attr_name = f"enable_{feature}" if not feature.startswith("enable_") else feature
        if hasattr(self, attr_name):
            return getattr(self, attr_name)
        return False


# ==================== Fraud Detection Config ====================

@dataclass
class FraudConfig:
    """Fraud detection thresholds and configuration"""
    high_risk_threshold: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_HIGH_RISK_THRESHOLD", "0.7"))
    )
    medium_risk_threshold: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_MEDIUM_RISK_THRESHOLD", "0.5"))
    )
    auto_deny_threshold: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_AUTO_DENY_THRESHOLD", "0.85"))
    )
    round_number_variance: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_ROUND_NUMBER_VARIANCE", "0.05"))
    )
    history_weight: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_HISTORY_WEIGHT", "0.2"))
    )
    pattern_weight: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_PATTERN_WEIGHT", "0.3"))
    )
    document_weight: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_DOCUMENT_WEIGHT", "0.25"))
    )
    behavior_weight: float = field(
        default_factory=lambda: float(os.getenv("FRAUD_BEHAVIOR_WEIGHT", "0.25"))
    )


# ==================== Rate Limiting Config ====================

@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    requests_per_minute: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    )
    requests_per_hour: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_HOUR", "1000"))
    )
    requests_per_day: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_DAY", "10000"))
    )
    burst_limit: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_BURST", "10"))
    )
    # Endpoint-specific overrides (JSON string)
    endpoint_overrides: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        overrides_str = os.getenv("RATE_LIMIT_ENDPOINT_OVERRIDES", "")
        if overrides_str:
            import json
            try:
                self.endpoint_overrides = json.loads(overrides_str)
            except json.JSONDecodeError:
                logger.warning("Invalid RATE_LIMIT_ENDPOINT_OVERRIDES JSON")


# ==================== Circuit Breaker Config ====================

@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration"""
    failure_threshold: int = field(
        default_factory=lambda: int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
    )
    success_threshold: int = field(
        default_factory=lambda: int(os.getenv("CB_SUCCESS_THRESHOLD", "3"))
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("CB_TIMEOUT_SECONDS", "60"))
    )
    half_open_max_calls: int = field(
        default_factory=lambda: int(os.getenv("CB_HALF_OPEN_MAX_CALLS", "3"))
    )


# ==================== Database Config ====================

@dataclass
class DatabaseConfig:
    """Database configuration settings"""
    host: str = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    name: str = field(default_factory=lambda: os.getenv("DB_NAME", "insurance_adjudicator"))
    user: str = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))
    pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "10")))
    max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "20")))
    ssl_mode: str = field(default_factory=lambda: os.getenv("DB_SSL_MODE", "prefer"))
    auto_create_tables: bool = field(
        default_factory=lambda: _env_bool(
            "DB_AUTO_CREATE_TABLES",
            os.getenv("ENVIRONMENT", "development").lower() != "production",
        )
    )

    @property
    def connection_string(self) -> str:
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.name}"

    @property
    def async_connection_string(self) -> str:
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"postgresql+asyncpg://{user}:{password}@{self.host}:{self.port}/{self.name}"


# ==================== Redis Config ====================

@dataclass
class RedisConfig:
    """Redis configuration for caching and message queuing"""
    host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    password: Optional[str] = field(default_factory=lambda: os.getenv("REDIS_PASSWORD"))
    db: int = field(default_factory=lambda: int(os.getenv("REDIS_DB", "0")))
    ssl: bool = field(default_factory=lambda: os.getenv("REDIS_SSL", "false").lower() == "true")

    @property
    def connection_string(self) -> str:
        protocol = "rediss" if self.ssl else "redis"
        auth = f":{quote_plus(self.password)}@" if self.password else ""
        return f"{protocol}://{auth}{self.host}:{self.port}/{self.db}"


# ==================== LLM Config ====================

@dataclass
class LLMConfig:
    """LLM Provider configuration"""
    provider: str = field(
        default_factory=lambda: os.getenv(
            "LLM_PROVIDER",
            "mock" if os.getenv("ENVIRONMENT", "development").lower() == "development" else "anthropic",
        )
    )
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-4-20250514"))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096")))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))
    timeout: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "120")))
    fallback_model: Optional[str] = field(default_factory=lambda: os.getenv("LLM_FALLBACK_MODEL"))
    max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "3")))


# ==================== Agent Config ====================

@dataclass
class AgentConfig:
    """Agent orchestration configuration"""
    max_iterations: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_ITERATIONS", "10")))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("AGENT_TIMEOUT", "300")))
    retry_attempts: int = field(default_factory=lambda: int(os.getenv("AGENT_RETRY_ATTEMPTS", "3")))
    parallel_agents: int = field(default_factory=lambda: int(os.getenv("AGENT_PARALLEL", "5")))
    confidence_threshold: float = field(default_factory=lambda: float(os.getenv("AGENT_CONFIDENCE_THRESHOLD", "0.85")))
    human_review_threshold: float = field(default_factory=lambda: float(os.getenv("AGENT_HUMAN_REVIEW_THRESHOLD", "0.70")))
    min_confidence_floor: float = field(default_factory=lambda: float(os.getenv("AGENT_MIN_CONFIDENCE_FLOOR", "0.40")))

    # Step-specific timeouts
    document_extraction_timeout: int = field(
        default_factory=lambda: int(os.getenv("AGENT_DOC_EXTRACTION_TIMEOUT", "60"))
    )
    policy_analysis_timeout: int = field(
        default_factory=lambda: int(os.getenv("AGENT_POLICY_ANALYSIS_TIMEOUT", "45"))
    )
    fraud_detection_timeout: int = field(
        default_factory=lambda: int(os.getenv("AGENT_FRAUD_DETECTION_TIMEOUT", "60"))
    )
    decision_timeout: int = field(
        default_factory=lambda: int(os.getenv("AGENT_DECISION_TIMEOUT", "45"))
    )


# ==================== Observability Config ====================

@dataclass
class ObservabilityConfig:
    """Logging and monitoring configuration"""
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_format: str = field(default_factory=lambda: os.getenv("LOG_FORMAT", "json"))
    enable_tracing: bool = field(default_factory=lambda: os.getenv("ENABLE_TRACING", "true").lower() == "true")
    metrics_port: int = field(default_factory=lambda: int(os.getenv("METRICS_PORT", "9090")))
    jaeger_endpoint: Optional[str] = field(default_factory=lambda: os.getenv("JAEGER_ENDPOINT"))
    prometheus_multiproc_dir: Optional[str] = field(
        default_factory=lambda: os.getenv("PROMETHEUS_MULTIPROC_DIR")
    )


# ==================== Security Config ====================

@dataclass
class SecurityConfig:
    """Security configuration"""
    encryption_key: Optional[str] = field(default_factory=lambda: os.getenv("ENCRYPTION_KEY"))
    jwt_secret: Optional[str] = field(default_factory=lambda: os.getenv("JWT_SECRET"))
    jwt_expiry_hours: int = field(default_factory=lambda: int(os.getenv("JWT_EXPIRY_HOURS", "24")))
    jwt_algorithm: str = field(default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256"))
    api_key_header: str = field(default_factory=lambda: os.getenv("API_KEY_HEADER", "X-API-Key"))
    admin_roles: List[str] = field(default_factory=lambda: ["admin", "superadmin"])
    reviewer_roles: List[str] = field(default_factory=lambda: ["admin", "reviewer", "senior_reviewer"])
    viewer_roles: List[str] = field(default_factory=lambda: ["admin", "reviewer", "viewer", "auditor"])
    cors_origins: List[str] = field(default_factory=list)
    allowed_hosts: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.cors_origins = _env_csv("CORS_ORIGINS", "*")
        self.allowed_hosts = _env_csv("ALLOWED_HOSTS", "*")


# ==================== Main Settings ====================

@dataclass
class Settings:
    """Main application settings with validation"""
    environment: Environment = field(
        default_factory=lambda: Environment(os.getenv("ENVIRONMENT", "development"))
    )
    app_name: str = "Insurance AI Adjudicator"
    version: str = "2.0.0"

    # Sub-configurations
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    fraud: FraudConfig = field(default_factory=FraudConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)

    # Cached validation state
    _validated: bool = field(default=False, repr=False)
    _validation_errors: List[str] = field(default_factory=list, repr=False)

    @property
    def api_keys(self) -> List[str]:
        """Get API keys from environment variable (comma-separated)"""
        keys_str = os.getenv("API_KEYS", "")
        if not keys_str:
            return []
        return [k.strip() for k in keys_str.split(",") if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT

    @property
    def is_staging(self) -> bool:
        return self.environment == Environment.STAGING

    def validate(self, raise_on_error: bool = True) -> List[str]:
        """
        Validate all configuration settings.

        Args:
            raise_on_error: Whether to raise ConfigurationError on validation failure

        Returns:
            List of validation error messages

        Raises:
            ConfigurationError: If validation fails and raise_on_error is True
        """
        if self._validated:
            return self._validation_errors

        errors = []

        # Production-specific validations
        if self.is_production:
            errors.extend(self._validate_production())

        # LLM configuration
        errors.extend(self._validate_llm())

        # Agent configuration
        errors.extend(self._validate_agent())

        # Fraud configuration
        errors.extend(self._validate_fraud())

        # Security configuration
        errors.extend(self._validate_security())

        self._validation_errors = errors
        self._validated = True

        if errors and raise_on_error:
            raise ConfigurationError(
                message=f"Configuration validation failed with {len(errors)} error(s)",
                details={"errors": errors},
            )

        if errors:
            for error in errors:
                logger.warning(f"Configuration warning: {error}")

        return errors

    def _validate_production(self) -> List[str]:
        """Validate production-specific requirements"""
        errors = []

        if not self.api_keys:
            errors.append("API_KEYS must be set in production")

        if not self.database.password:
            errors.append("DB_PASSWORD must be set in production")
        elif _looks_like_placeholder(self.database.password):
            errors.append("DB_PASSWORD must not use a development placeholder in production")

        if self.llm.provider == "mock":
            errors.append("LLM_PROVIDER=mock is not allowed in production")

        if self.llm.provider != "mock" and not self.llm.api_key:
            errors.append("LLM_API_KEY must be set in production")

        if not self.security.encryption_key:
            errors.append("ENCRYPTION_KEY must be set in production for PII protection")

        if not self.security.jwt_secret:
            errors.append("JWT_SECRET must be set in production")
        elif _looks_like_placeholder(self.security.jwt_secret) or len(self.security.jwt_secret) < 32:
            errors.append("JWT_SECRET must be at least 32 characters and not a placeholder")

        weak_api_keys = [
            key for key in self.api_keys
            if _looks_like_placeholder(key) or len(key) < 24
        ]
        if weak_api_keys:
            errors.append("API_KEYS must be high-entropy values of at least 24 characters")

        if not self.security.allowed_hosts or "*" in self.security.allowed_hosts:
            errors.append("ALLOWED_HOSTS must be explicit in production")

        if self.features.use_database and not self.database.password:
            errors.append("Database password required when FF_USE_DATABASE is enabled")

        if self.database.auto_create_tables:
            errors.append("DB_AUTO_CREATE_TABLES must be false in production; run Alembic migrations instead")

        if self.database.ssl_mode in {"disable", "allow"}:
            errors.append("DB_SSL_MODE must require or prefer TLS in production")

        if self.features.enable_rate_limiting and not self.redis.host:
            errors.append("REDIS_HOST must be set when rate limiting is enabled in production")

        return errors

    def _validate_llm(self) -> List[str]:
        """Validate LLM configuration"""
        errors = []

        if self.llm.provider not in ["anthropic", "openai", "mock"]:
            errors.append(f"Invalid LLM_PROVIDER: {self.llm.provider}")

        if self.llm.provider != "mock" and self.llm.api_key and _looks_like_placeholder(self.llm.api_key):
            errors.append("LLM_API_KEY must not use a placeholder value")

        if self.llm.temperature < 0 or self.llm.temperature > 2:
            errors.append(f"LLM_TEMPERATURE must be between 0 and 2: {self.llm.temperature}")

        if self.llm.max_tokens < 1 or self.llm.max_tokens > 100000:
            errors.append(f"LLM_MAX_TOKENS must be between 1 and 100000: {self.llm.max_tokens}")

        if self.llm.timeout < 1:
            errors.append(f"LLM_TIMEOUT must be positive: {self.llm.timeout}")

        return errors

    def _validate_agent(self) -> List[str]:
        """Validate agent configuration"""
        errors = []

        if self.agent.confidence_threshold < 0 or self.agent.confidence_threshold > 1:
            errors.append(f"AGENT_CONFIDENCE_THRESHOLD must be between 0 and 1")

        if self.agent.human_review_threshold < 0 or self.agent.human_review_threshold > 1:
            errors.append(f"AGENT_HUMAN_REVIEW_THRESHOLD must be between 0 and 1")

        if self.agent.min_confidence_floor < 0 or self.agent.min_confidence_floor > 1:
            errors.append(f"AGENT_MIN_CONFIDENCE_FLOOR must be between 0 and 1")

        if self.agent.min_confidence_floor > self.agent.human_review_threshold:
            errors.append(
                f"AGENT_MIN_CONFIDENCE_FLOOR ({self.agent.min_confidence_floor}) "
                f"should not exceed AGENT_HUMAN_REVIEW_THRESHOLD ({self.agent.human_review_threshold})"
            )

        if self.agent.human_review_threshold > self.agent.confidence_threshold:
            errors.append(
                f"AGENT_HUMAN_REVIEW_THRESHOLD ({self.agent.human_review_threshold}) "
                f"should not exceed AGENT_CONFIDENCE_THRESHOLD ({self.agent.confidence_threshold})"
            )

        return errors

    def _validate_fraud(self) -> List[str]:
        """Validate fraud detection configuration"""
        errors = []

        weights_sum = (
            self.fraud.history_weight +
            self.fraud.pattern_weight +
            self.fraud.document_weight +
            self.fraud.behavior_weight
        )
        if abs(weights_sum - 1.0) > 0.01:
            errors.append(
                f"Fraud detection weights must sum to 1.0, got {weights_sum:.2f}"
            )

        if self.fraud.medium_risk_threshold >= self.fraud.high_risk_threshold:
            errors.append(
                f"FRAUD_MEDIUM_RISK_THRESHOLD ({self.fraud.medium_risk_threshold}) "
                f"must be less than FRAUD_HIGH_RISK_THRESHOLD ({self.fraud.high_risk_threshold})"
            )

        return errors

    def _validate_security(self) -> List[str]:
        """Validate security configuration"""
        errors = []

        if self.is_production and "*" in self.security.cors_origins:
            errors.append("CORS_ORIGINS should not be '*' in production")

        if self.security.jwt_algorithm and self.security.jwt_algorithm.lower() == "none":
            errors.append("JWT_ALGORITHM=none is not allowed")

        if self.security.encryption_key:
            try:
                decoded = base64.urlsafe_b64decode(self.security.encryption_key)
                if len(decoded) != 32:
                    errors.append("ENCRYPTION_KEY must decode to exactly 32 bytes")
            except Exception:
                errors.append("ENCRYPTION_KEY must be urlsafe base64 encoded")

        return errors

    def get_step_timeout(self, step: str) -> int:
        """Get timeout for a specific workflow step"""
        timeouts = {
            "document_extraction": self.agent.document_extraction_timeout,
            "policy_analysis": self.agent.policy_analysis_timeout,
            "fraud_detection": self.agent.fraud_detection_timeout,
            "decision": self.agent.decision_timeout,
        }
        return timeouts.get(step, self.agent.timeout_seconds)

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """Convert settings to dictionary, optionally masking secrets"""
        result = {
            "environment": self.environment.value,
            "app_name": self.app_name,
            "version": self.version,
            "features": {
                "fraud_detection": self.features.enable_fraud_detection,
                "document_extraction": self.features.enable_document_extraction,
                "auto_adjudication": self.features.enable_auto_adjudication,
                "parallel_agents": self.features.enable_parallel_agents,
                "pii_encryption": self.features.enable_pii_encryption,
                "audit_logging": self.features.enable_audit_logging,
                "rate_limiting": self.features.enable_rate_limiting,
                "use_database": self.features.use_database,
            },
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "name": self.database.name,
                "pool_size": self.database.pool_size,
            },
            "llm": {
                "provider": self.llm.provider,
                "model": self.llm.model,
                "max_tokens": self.llm.max_tokens,
                "temperature": self.llm.temperature,
            },
            "agent": {
                "max_iterations": self.agent.max_iterations,
                "timeout_seconds": self.agent.timeout_seconds,
                "confidence_threshold": self.agent.confidence_threshold,
                "human_review_threshold": self.agent.human_review_threshold,
            },
        }

        if not mask_secrets:
            result["database"]["password"] = self.database.password
            result["llm"]["api_key"] = self.llm.api_key

        return result


# ==================== Global Settings Instance ====================

def _create_settings() -> Settings:
    """Create and optionally validate settings"""
    settings = Settings()

    # Validate in production, warn in other environments
    try:
        settings.validate(raise_on_error=settings.is_production)
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        raise

    return settings


settings = _create_settings()
