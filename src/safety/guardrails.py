"""
Safety Guardrails Module

Implements allowlist enforcement, risky action classification,
and sensitive data redaction for regulated financial environments.
"""

import re
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse
import json

from src.config import settings
from src.artifacts.schema import ActionType, ActionStep


class RiskLevel(str, Enum):
    SAFE = "safe"           # Reversible, read-only, navigation
    CAUTION = "caution"     # Data entry, form submission
    RISKY = "risky"         # Irreversible, financial impact
    CRITICAL = "critical"   # Requires explicit human confirmation


class GuardrailViolation(Exception):
    """Raised when an action violates safety guardrails."""
    def __init__(self, message: str, violation_type: str, details: Dict[str, Any] = None):
        super().__init__(message)
        self.violation_type = violation_type
        self.details = details or {}


@dataclass
class ActionRiskProfile:
    """Risk profile for an action type."""
    action_type: ActionType
    risk_level: RiskLevel
    requires_confirmation: bool = False
    allowed_contexts: List[str] = field(default_factory=list)  # e.g., ["read_only", "form_entry"]
    blocked_contexts: List[str] = field(default_factory=list)
    description: str = ""


# Default risk profiles for action types
DEFAULT_RISK_PROFILES = {
    ActionType.NAVIGATE: ActionRiskProfile(
        action_type=ActionType.NAVIGATE,
        risk_level=RiskLevel.SAFE,
        description="Navigation to allowed URLs"
    ),
    ActionType.CLICK: ActionRiskProfile(
        action_type=ActionType.CLICK,
        risk_level=RiskLevel.CAUTION,
        description="Clicking elements - context dependent"
    ),
    ActionType.TYPE: ActionRiskProfile(
        action_type=ActionType.TYPE,
        risk_level=RiskLevel.CAUTION,
        description="Text input - may include sensitive data"
    ),
    ActionType.SELECT: ActionRiskProfile(
        action_type=ActionType.SELECT,
        risk_level=RiskLevel.CAUTION,
        description="Dropdown selection"
    ),
    ActionType.SCROLL: ActionRiskProfile(
        action_type=ActionType.SCROLL,
        risk_level=RiskLevel.SAFE,
        description="Scrolling - read only"
    ),
    ActionType.WAIT: ActionRiskProfile(
        action_type=ActionType.WAIT,
        risk_level=RiskLevel.SAFE,
        description="Waiting - no side effects"
    ),
    ActionType.EXTRACT: ActionRiskProfile(
        action_type=ActionType.EXTRACT,
        risk_level=RiskLevel.SAFE,
        description="Data extraction - read only"
    ),
    ActionType.HOVER: ActionRiskProfile(
        action_type=ActionType.HOVER,
        risk_level=RiskLevel.SAFE,
        description="Hover - no side effects"
    ),
    ActionType.KEY_PRESS: ActionRiskProfile(
        action_type=ActionType.KEY_PRESS,
        risk_level=RiskLevel.CAUTION,
        description="Keyboard input"
    ),
}


# Patterns for sensitive data detection (financial/PII)
SENSITIVE_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "routing_number": re.compile(r"\b\d{9}\b"),
    "account_number": re.compile(r"\b\d{10,17}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
    "date_of_birth": re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    "api_key": re.compile(r"\b[A-Za-z0-9]{32,}\b"),
    "password": re.compile(r"(?i)(password|passwd|pwd|secret|token|key)\s*[:=]\s*\S+"),
}


class AllowlistManager:
    """Manages allowlists for domains, actions, and selectors."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.allowed_domains: Set[str] = set(settings.allowed_domains)
        self.allowed_action_types: Set[str] = set(settings.allowed_action_types)
        self.risky_action_types: Set[str] = set(settings.risky_action_types)
        self.custom_allowed_domains: Set[str] = set()
        self.custom_blocked_domains: Set[str] = set()
        self.allowed_selectors: Dict[str, List[str]] = {}  # domain -> allowed selector patterns
        self.blocked_selectors: Dict[str, List[str]] = {}  # domain -> blocked selector patterns
    
    def add_allowed_domain(self, domain: str) -> None:
        """Add a domain to the allowlist."""
        self.custom_allowed_domains.add(domain.lower())
    
    def add_blocked_domain(self, domain: str) -> None:
        """Add a domain to the blocklist."""
        self.custom_blocked_domains.add(domain.lower())
    
    def is_domain_allowed(self, url: str) -> bool:
        """Check if a URL's domain is allowed."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Check blocklist first
        for blocked in self.custom_blocked_domains:
            if blocked in domain:
                return False
        
        # Check allowlist
        for allowed in self.allowed_domains:
            if allowed in domain:
                return True
        for allowed in self.custom_allowed_domains:
            if allowed in domain:
                return True
        
        return False
    
    def is_action_allowed(self, action_type: ActionType) -> bool:
        """Check if an action type is allowed."""
        return action_type.value in self.allowed_action_types
    
    def is_action_risky(self, action_type: ActionType) -> bool:
        """Check if an action type is considered risky."""
        return action_type.value in self.risky_action_types
    
    def is_selector_allowed(self, domain: str, selector: str) -> bool:
        """Check if a selector is allowed for a domain."""
        domain = domain.lower()
        
        # Check blocked selectors
        for pattern in self.blocked_selectors.get(domain, []):
            if re.search(pattern, selector):
                return False
        
        # If allowed selectors defined, check against them
        allowed = self.allowed_selectors.get(domain)
        if allowed:
            for pattern in allowed:
                if re.search(pattern, selector):
                    return True
            return False
        
        return True  # Default allow if no specific rules
    
    def get_risk_profile(self, action_type: ActionType) -> ActionRiskProfile:
        """Get risk profile for an action type."""
        return DEFAULT_RISK_PROFILES.get(action_type, ActionRiskProfile(
            action_type=action_type,
            risk_level=RiskLevel.CAUTION,
            description="Unknown action type"
        ))


class SensitiveDataRedactor:
    """Redacts sensitive data from logs, artifacts, and evidence."""
    
    def __init__(self, custom_patterns: Dict[str, re.Pattern] = None):
        self.patterns = SENSITIVE_PATTERNS.copy()
        if custom_patterns:
            self.patterns.update(custom_patterns)
    
    def redact(self, text: str) -> str:
        """Redact sensitive data from text."""
        if not isinstance(text, str):
            text = str(text)
        
        redacted = text
        for pattern_name, pattern in self.patterns.items():
            redacted = pattern.sub(f"[REDACTED_{pattern_name.upper()}]", redacted)
        
        return redacted
    
    def redact_dict(self, data: Dict[str, Any], sensitive_keys: Set[str] = None) -> Dict[str, Any]:
        """Recursively redact sensitive data from a dictionary."""
        if sensitive_keys is None:
            sensitive_keys = {
                "password", "secret", "token", "key", "api_key", "apikey",
                "ssn", "social_security", "credit_card", "card_number",
                "account_number", "routing_number", "pin", "cvv",
                "date_of_birth", "dob", "driver_license", "passport"
            }
        
        result = {}
        for key, value in data.items():
            key_lower = key.lower()
            
            # Check if key itself is sensitive
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                result[key] = "[REDACTED]"
            elif isinstance(value, str):
                result[key] = self.redact(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value, sensitive_keys)
            elif isinstance(value, list):
                result[key] = [
                    self.redact_dict(item, sensitive_keys) if isinstance(item, dict)
                    else self.redact(item) if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        
        return result
    
    def redact_artifact_inputs(self, inputs: Dict[str, Any], artifact) -> Dict[str, Any]:
        """Redact artifact inputs based on parameter sensitivity flags."""
        result = {}
        sensitive_names = {p.name for p in artifact.inputs if p.sensitive}
        
        for key, value in inputs.items():
            if key in sensitive_names:
                result[key] = "[REDACTED]"
            elif isinstance(value, str):
                result[key] = self.redact(value)
            else:
                result[key] = value
        
        return result


class SafetyEnforcer:
    """
    Main safety enforcement class.
    
    Checks every action against guardrails before execution.
    """
    
    def __init__(self, allowlist: AllowlistManager = None, redactor: SensitiveDataRedactor = None):
        self.allowlist = allowlist or AllowlistManager()
        self.redactor = redactor or SensitiveDataRedactor()
        self.violation_log: List[Dict[str, Any]] = []
    
    def check_navigation(self, url: str) -> None:
        """Check if navigation is allowed."""
        if not self.allowlist.is_domain_allowed(url):
            raise GuardrailViolation(
                f"Navigation to {url} not allowed by domain policy",
                "domain_violation",
                {"url": url, "allowed_domains": list(self.allowlist.allowed_domains)}
            )
    
    def check_action(self, step: ActionStep, current_url: str) -> None:
        """Check if an action step is allowed."""
        # Check action type
        if not self.allowlist.is_action_allowed(step.action):
            raise GuardrailViolation(
                f"Action type {step.action.value} not allowed",
                "action_type_violation",
                {"action": step.action.value, "allowed": list(self.allowlist.allowed_action_types)}
            )
        
        # Check domain for navigation
        if step.action == ActionType.NAVIGATE and step.url:
            self.check_navigation(step.url)
        
        # Check if risky action requires confirmation
        risk_profile = self.allowlist.get_risk_profile(step.action)
        if risk_profile.risk_level in (RiskLevel.RISKY, RiskLevel.CRITICAL):
            if not step.requires_confirmation:
                raise GuardrailViolation(
                    f"Risky action {step.action.value} requires explicit confirmation",
                    "confirmation_required",
                    {"action": step.action.value, "risk_level": risk_profile.risk_level.value}
                )
        
        # Check selector if targeting an element
        if step.target:
            parsed = urlparse(current_url)
            domain = parsed.netloc.lower()
            # Extract selector from target for checking
            selector_str = self._target_to_selector_string(step.target)
            if not self.allowlist.is_selector_allowed(domain, selector_str):
                raise GuardrailViolation(
                    f"Selector not allowed for domain {domain}",
                    "selector_violation",
                    {"domain": domain, "selector": selector_str}
                )
    
    def _target_to_selector_string(self, target) -> str:
        """Convert ElementLocator to string for checking."""
        parts = [target.primary, target.primary_value]
        for fb in target.fallbacks:
            parts.extend([fb["strategy"], fb["value"]])
        return "|".join(parts)
    
    def sanitize_for_logging(self, data: Any) -> Any:
        """Sanitize data for safe logging."""
        if isinstance(data, dict):
            return self.redactor.redact_dict(data)
        elif isinstance(data, str):
            return self.redactor.redact(data)
        elif isinstance(data, list):
            return [self.sanitize_for_logging(item) for item in data]
        return data
    
    def sanitize_artifact(self, artifact) -> Any:
        """Create a safe copy of artifact for logging/storage."""
        # Redact sensitive inputs
        artifact_dict = artifact.model_dump()
        
        # Redact any sensitive values in steps
        for step in artifact_dict.get("steps", []):
            if step.get("value") and isinstance(step["value"], str):
                step["value"] = self.redactor.redact(step["value"])
        
        return artifact_dict
    
    def log_violation(self, violation: GuardrailViolation) -> None:
        """Log a guardrail violation."""
        self.violation_log.append({
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            "violation_type": violation.violation_type,
            "message": str(violation),
            "details": violation.details,
        })
    
    def get_violations(self) -> List[Dict[str, Any]]:
        """Get all logged violations."""
        return self.violation_log.copy()


# Global instances
allowlist_manager = AllowlistManager()
redactor = SensitiveDataRedactor()
safety_enforcer = SafetyEnforcer(allowlist_manager, redactor)