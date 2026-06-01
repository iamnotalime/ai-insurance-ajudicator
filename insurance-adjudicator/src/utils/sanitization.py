"""
Input Sanitization Utilities for Insurance Adjudication System
Protects against prompt injection and malicious input
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from html import escape


logger = logging.getLogger(__name__)


@dataclass
class SanitizationResult:
    """Result of input sanitization"""
    sanitized_text: str
    was_modified: bool
    warnings: List[str]
    blocked: bool = False
    block_reason: Optional[str] = None


class PromptSanitizer:
    """
    Sanitizes user input before sending to LLM.

    Protects against:
    - Prompt injection attacks
    - Jailbreak attempts
    - Malicious instruction injection
    - PII leakage in prompts
    """

    # Patterns that might indicate prompt injection
    INJECTION_PATTERNS = [
        # Direct instruction override attempts
        r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
        r"(?i)disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?)",
        r"(?i)forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?)",
        r"(?i)new\s+instructions?:",
        r"(?i)system\s*:\s*you\s+are",
        r"(?i)assistant\s*:\s*",
        r"(?i)user\s*:\s*",

        # Role manipulation
        r"(?i)act\s+as\s+(if\s+you\s+are\s+)?a?\s*(different|new|another)",
        r"(?i)pretend\s+(you\s+are|to\s+be)\s+(a\s+)?(different|new)",
        r"(?i)roleplay\s+as",
        r"(?i)you\s+are\s+now\s+a",

        # Delimiter manipulation
        r"```\s*system",
        r"<\|?system\|?>",
        r"\[INST\]",
        r"\[/INST\]",
        r"<<SYS>>",
        r"<</SYS>>",

        # Output manipulation
        r"(?i)output\s+the\s+(following|this)",
        r"(?i)print\s+(exactly|verbatim)",
        r"(?i)repeat\s+after\s+me",

        # Information extraction
        r"(?i)what\s+(are|were)\s+your\s+(instructions?|prompts?|rules?)",
        r"(?i)show\s+me\s+your\s+(system|initial)\s+prompt",
        r"(?i)reveal\s+your\s+(system|initial)\s+prompt",
    ]

    # Suspicious but not blocking patterns
    WARNING_PATTERNS = [
        r"(?i)bypass",
        r"(?i)override",
        r"(?i)jailbreak",
        r"(?i)DAN\s+mode",
        r"(?i)developer\s+mode",
    ]

    # PII patterns to redact
    PII_PATTERNS = {
        "ssn": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
        "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
        "phone": r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    }

    def __init__(
        self,
        block_injections: bool = True,
        redact_pii: bool = True,
        max_length: int = 50000,
    ):
        self.block_injections = block_injections
        self.redact_pii = redact_pii
        self.max_length = max_length

        # Compile patterns for efficiency
        self._injection_patterns = [
            re.compile(p) for p in self.INJECTION_PATTERNS
        ]
        self._warning_patterns = [
            re.compile(p) for p in self.WARNING_PATTERNS
        ]
        self._pii_patterns = {
            name: re.compile(pattern)
            for name, pattern in self.PII_PATTERNS.items()
        }

    def sanitize(self, text: str) -> SanitizationResult:
        """
        Sanitize input text for LLM consumption.

        Args:
            text: Raw input text

        Returns:
            SanitizationResult with sanitized text and metadata
        """
        warnings = []
        was_modified = False
        blocked = False
        block_reason = None

        if not text:
            return SanitizationResult(
                sanitized_text="",
                was_modified=False,
                warnings=[],
            )

        # Check length
        if len(text) > self.max_length:
            text = text[: self.max_length]
            was_modified = True
            warnings.append(f"Input truncated to {self.max_length} characters")

        # Check for injection attempts
        if self.block_injections:
            for pattern in self._injection_patterns:
                if pattern.search(text):
                    logger.warning(
                        f"Potential prompt injection detected: {pattern.pattern}"
                    )
                    blocked = True
                    block_reason = "Potential prompt injection detected"
                    break

        # Check for warning patterns
        for pattern in self._warning_patterns:
            if pattern.search(text):
                warnings.append(f"Suspicious pattern detected: {pattern.pattern}")

        # Redact PII if enabled
        if self.redact_pii and not blocked:
            text, pii_warnings = self._redact_pii(text)
            if pii_warnings:
                was_modified = True
                warnings.extend(pii_warnings)

        # Escape special characters that might interfere with prompts
        if not blocked:
            text = self._escape_special(text)

        return SanitizationResult(
            sanitized_text=text if not blocked else "",
            was_modified=was_modified,
            warnings=warnings,
            blocked=blocked,
            block_reason=block_reason,
        )

    def _redact_pii(self, text: str) -> tuple[str, List[str]]:
        """Redact PII from text"""
        warnings = []
        result = text

        for pii_type, pattern in self._pii_patterns.items():
            matches = pattern.findall(result)
            if matches:
                result = pattern.sub(f"[REDACTED_{pii_type.upper()}]", result)
                warnings.append(f"Redacted {len(matches)} {pii_type} value(s)")

        return result, warnings

    def _escape_special(self, text: str) -> str:
        """Escape special characters"""
        # Escape angle brackets that might be interpreted as special tokens
        text = text.replace("<|", "< |")
        text = text.replace("|>", "| >")

        # Escape triple backticks that might break code blocks
        text = text.replace("```", "` ` `")

        return text


class ContentFilter:
    """
    Filters content for appropriateness and policy compliance.
    """

    # Categories of blocked content
    BLOCKED_CATEGORIES = {
        "fraud_instruction": [
            r"(?i)how\s+to\s+(commit|do)\s+fraud",
            r"(?i)help\s+me\s+(commit|do)\s+fraud",
            r"(?i)fake\s+(a\s+)?claim",
            r"(?i)falsify\s+(documents?|records?)",
        ],
        "identity_theft": [
            r"(?i)steal\s+(someone'?s?|an?)\s+identity",
            r"(?i)fake\s+(id|identification)",
        ],
        "data_exfiltration": [
            r"(?i)export\s+all\s+(user|customer)\s+data",
            r"(?i)dump\s+the\s+database",
        ],
    }

    def __init__(self):
        self._patterns = {
            category: [re.compile(p) for p in patterns]
            for category, patterns in self.BLOCKED_CATEGORIES.items()
        }

    def check(self, text: str) -> tuple[bool, Optional[str]]:
        """
        Check content against filters.

        Returns:
            Tuple of (is_allowed, blocked_category)
        """
        for category, patterns in self._patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    logger.warning(f"Content blocked: {category}")
                    return False, category

        return True, None


class LLMInputPreparer:
    """
    Prepares input for LLM by combining sanitization and formatting.
    """

    def __init__(
        self,
        sanitizer: Optional[PromptSanitizer] = None,
        content_filter: Optional[ContentFilter] = None,
    ):
        self.sanitizer = sanitizer or PromptSanitizer()
        self.content_filter = content_filter or ContentFilter()

    def prepare_user_input(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, List[str]]:
        """
        Prepare user input for inclusion in LLM prompt.

        Args:
            user_input: Raw user input
            context: Optional context to validate against

        Returns:
            Tuple of (prepared_input, warnings)

        Raises:
            ValueError: If input is blocked
        """
        # Sanitize input
        result = self.sanitizer.sanitize(user_input)

        if result.blocked:
            raise ValueError(f"Input blocked: {result.block_reason}")

        # Check content filter
        is_allowed, blocked_category = self.content_filter.check(result.sanitized_text)
        if not is_allowed:
            raise ValueError(f"Input blocked by content filter: {blocked_category}")

        # Wrap user input clearly to prevent confusion with instructions
        wrapped = self._wrap_user_input(result.sanitized_text)

        return wrapped, result.warnings

    def _wrap_user_input(self, text: str) -> str:
        """Wrap user input to clearly delineate it from instructions"""
        return f"<user_provided_content>\n{text}\n</user_provided_content>"

    def prepare_claim_data(self, claim_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare claim data for inclusion in LLM context.

        Sanitizes string fields within the claim data.
        """
        sanitized = {}

        for key, value in claim_data.items():
            if isinstance(value, str):
                result = self.sanitizer.sanitize(value)
                sanitized[key] = result.sanitized_text
            elif isinstance(value, dict):
                sanitized[key] = self.prepare_claim_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self.prepare_claim_data(item) if isinstance(item, dict)
                    else self.sanitizer.sanitize(item).sanitized_text if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                sanitized[key] = value

        return sanitized


# Global instances
_sanitizer: Optional[PromptSanitizer] = None
_content_filter: Optional[ContentFilter] = None
_input_preparer: Optional[LLMInputPreparer] = None


def get_sanitizer() -> PromptSanitizer:
    """Get global prompt sanitizer"""
    global _sanitizer
    if _sanitizer is None:
        _sanitizer = PromptSanitizer()
    return _sanitizer


def get_content_filter() -> ContentFilter:
    """Get global content filter"""
    global _content_filter
    if _content_filter is None:
        _content_filter = ContentFilter()
    return _content_filter


def get_input_preparer() -> LLMInputPreparer:
    """Get global input preparer"""
    global _input_preparer
    if _input_preparer is None:
        _input_preparer = LLMInputPreparer()
    return _input_preparer


def sanitize_input(text: str, *, redact_pii: bool = True, max_length: int = 50000) -> str:
    """
    Compatibility helper that returns sanitized text directly.

    Raises:
        ValueError: If prompt-injection or blocked content is detected.
    """
    sanitizer = PromptSanitizer(redact_pii=redact_pii, max_length=max_length)
    result = sanitizer.sanitize(text)
    if result.blocked:
        raise ValueError(result.block_reason or "Input blocked")
    allowed, category = get_content_filter().check(result.sanitized_text)
    if not allowed:
        raise ValueError(f"Input blocked by content filter: {category}")
    return result.sanitized_text


def detect_prompt_injection(text: str) -> bool:
    """Return True when text matches a prompt-injection pattern."""
    if not text:
        return False
    sanitizer = get_sanitizer()
    return any(pattern.search(text) for pattern in sanitizer._injection_patterns)
