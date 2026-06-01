"""
Core Utilities Module
Consolidates common functionality used across the codebase
"""

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, TypeVar, Type
from uuid import uuid4

from pydantic import BaseModel


logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# =============================================================================
# JSON Parsing Utilities
# =============================================================================

def parse_json_from_llm_response(
    response: str,
    default: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Safely parse JSON from LLM response with multiple fallback strategies.

    This is a centralized utility used across all agents for parsing
    structured data from LLM responses.

    Args:
        response: The raw LLM response text
        default: Default value to return if parsing fails
        strict: If True, raise exception on parse failure instead of returning default

    Returns:
        Parsed JSON dict or default value

    Raises:
        ValueError: If strict=True and parsing fails
    """
    if default is None:
        default = {}

    if not response:
        if strict:
            raise ValueError("Empty response cannot be parsed as JSON")
        return default

    # Strategy 1: Try to parse the entire response as JSON
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Strategy 2: Look for JSON in code blocks (```json ... ```)
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find the outermost balanced braces
    try:
        start_idx = response.find('{')
        if start_idx != -1:
            depth = 0
            end_idx = start_idx
            in_string = False
            escape_next = False

            for i, char in enumerate(response[start_idx:], start_idx):
                if escape_next:
                    escape_next = False
                    continue

                if char == '\\' and in_string:
                    escape_next = True
                    continue

                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue

                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break

            if depth == 0:
                json_str = response[start_idx:end_idx + 1]
                return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 4: Try to find any valid JSON object (greedy but balanced)
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.findall(json_pattern, response)
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    if strict:
        raise ValueError(f"Failed to parse JSON from LLM response: {response[:200]}...")

    logger.warning(f"Failed to parse JSON from LLM response: {response[:200]}...")
    return default


def safe_json_serialize(obj: Any) -> str:
    """
    Safely serialize an object to JSON, handling special types.

    Args:
        obj: Object to serialize

    Returns:
        JSON string
    """
    def default_serializer(o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    return json.dumps(obj, default=default_serializer, indent=2)


# =============================================================================
# Claim Number Generation
# =============================================================================

def generate_claim_number(prefix: str = "CLM") -> str:
    """
    Generate a unique claim number.

    Format: {PREFIX}-{YYYYMMDD}-{8-char-hex}
    Example: CLM-20240115-A1B2C3D4

    Args:
        prefix: Prefix for the claim number

    Returns:
        Unique claim number string
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    unique_id = uuid4().hex[:8].upper()
    return f"{prefix}-{timestamp}-{unique_id}"


def generate_workflow_id() -> str:
    """Generate a unique workflow ID."""
    return f"WF-{uuid4().hex[:12].upper()}"


def generate_batch_id() -> str:
    """Generate a unique batch ID."""
    return f"BATCH-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


# =============================================================================
# Amount Validation and Formatting
# =============================================================================

def validate_claim_amount(
    amount: Decimal,
    min_amount: Decimal = Decimal("0"),
    max_amount: Decimal = Decimal("10000000"),
) -> tuple[bool, Optional[str]]:
    """
    Validate a claim amount.

    Args:
        amount: Amount to validate
        min_amount: Minimum allowed amount
        max_amount: Maximum allowed amount

    Returns:
        Tuple of (is_valid, error_message)
    """
    if amount < min_amount:
        return False, f"Amount must be at least ${min_amount}"
    if amount > max_amount:
        return False, f"Amount exceeds maximum limit of ${max_amount}"
    return True, None


def format_currency(amount: Decimal, currency: str = "USD") -> str:
    """
    Format a decimal amount as currency string.

    Args:
        amount: Amount to format
        currency: Currency code

    Returns:
        Formatted currency string
    """
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def is_round_number(amount: Decimal, threshold: Decimal = Decimal("100")) -> bool:
    """
    Check if an amount is suspiciously round (potential fraud indicator).

    Args:
        amount: Amount to check
        threshold: Minimum amount to check

    Returns:
        True if amount appears suspiciously round
    """
    if amount <= threshold:
        return False

    # Exact multiples of 1000
    if amount == amount.quantize(Decimal("1000")):
        return True
    # Exact multiples of 500
    if amount == amount.quantize(Decimal("500")):
        return True
    # Ends in .00 and is a multiple of 100
    if amount == amount.quantize(Decimal("100")) and amount % 100 == 0:
        return True
    # Just under round numbers (999, 1999, 4999)
    if (amount + 1) % 1000 == 0:
        return True

    return False


# =============================================================================
# Date Utilities
# =============================================================================

def ensure_timezone_aware(dt: datetime) -> datetime:
    """
    Ensure a datetime object is timezone-aware (UTC).

    Args:
        dt: Datetime object to check

    Returns:
        Timezone-aware datetime
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def days_between(start_date: datetime, end_date: datetime) -> int:
    """
    Calculate days between two dates.

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        Number of days between dates
    """
    start = ensure_timezone_aware(start_date) if isinstance(start_date, datetime) else start_date
    end = ensure_timezone_aware(end_date) if isinstance(end_date, datetime) else end_date
    return (end - start).days


def is_future_date(dt: datetime) -> bool:
    """Check if a date is in the future."""
    now = datetime.now(timezone.utc)
    dt = ensure_timezone_aware(dt) if isinstance(dt, datetime) else dt
    return dt > now


# =============================================================================
# Collection Utilities
# =============================================================================

def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    """
    Split a list into chunks of specified size.

    Args:
        lst: List to chunk
        chunk_size: Size of each chunk

    Returns:
        List of chunks
    """
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def deduplicate_by_key(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """
    Remove duplicates from a list of dicts based on a key.

    Args:
        items: List of dictionaries
        key: Key to use for deduplication

    Returns:
        Deduplicated list
    """
    seen = set()
    result = []
    for item in items:
        value = item.get(key)
        if value not in seen:
            seen.add(value)
            result.append(item)
    return result


def safe_get_nested(obj: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """
    Safely get a nested value from a dictionary.

    Args:
        obj: Dictionary to traverse
        *keys: Keys to traverse
        default: Default value if not found

    Returns:
        Found value or default
    """
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current


# =============================================================================
# String Utilities
# =============================================================================

def truncate_string(s: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length.

    Args:
        s: String to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix


def sanitize_for_logging(s: str, max_length: int = 500) -> str:
    """
    Sanitize a string for safe logging (remove PII patterns, truncate).

    Args:
        s: String to sanitize
        max_length: Maximum length

    Returns:
        Sanitized string
    """
    # Remove common PII patterns
    s = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', s)  # SSN
    s = re.sub(r'\b\d{16}\b', '[CARD]', s)  # Credit card
    s = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', s)  # Email

    return truncate_string(s, max_length)


# =============================================================================
# Retry Utilities
# =============================================================================

class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number (0-indexed)."""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)


# =============================================================================
# Validation Helpers
# =============================================================================

def validate_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    import uuid
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def validate_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_policy_number(policy_number: str) -> bool:
    """Validate policy number format."""
    # Format: POL-YYYY-XXX or similar
    pattern = r'^[A-Z]{2,4}-\d{4}-\d{3,6}$'
    return bool(re.match(pattern, policy_number))
