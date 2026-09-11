"""
Artifact Schema for Computer-Use Automation System

This module defines the typed, versioned schema for automation artifacts.
Artifacts are the reusable capabilities that AI agents invoke in production.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Optional, Literal, Union
from enum import Enum
from datetime import datetime
import uuid


class ActionType(str, Enum):
    """Types of actions the automation can perform."""
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    WAIT = "wait"
    EXTRACT = "extract"
    HOVER = "hover"
    KEY_PRESS = "key_press"


class LocatorStrategy(str, Enum):
    """Strategies for locating elements, ordered by robustness preference."""
    TEST_ID = "test_id"           # data-testid attribute (most robust)
    ARIA_LABEL = "aria_label"     # Accessible name
    ROLE_AND_NAME = "role_and_name"  # ARIA role + accessible name
    TEXT_CONTENT = "text_content"    # Visible text (fragile but sometimes necessary)
    CSS_SELECTOR = "css_selector"    # CSS selector (brittle)
    XPATH = "xpath"                  # XPath (brittle)
    COORDINATES = "coordinates"      # Screen coordinates (last resort)


class ElementLocator(BaseModel):
    """
    Robust element identification with fallback strategies.
    
    The system tries strategies in order of robustness.
    Multiple strategies can be provided for resilience.
    """
    primary: LocatorStrategy = Field(..., description="Primary locator strategy")
    primary_value: str = Field(..., description="Value for primary strategy")
    fallbacks: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Fallback locators: [{'strategy': '...', 'value': '...'}]"
    )
    frame_context: Optional[str] = Field(
        default=None,
        description="Frame/iframe context if element is nested"
    )
    description: str = Field(
        default="",
        description="Human-readable description of the target element"
    )
    
    def to_playwright_locator(self, page) -> Any:
        """Convert to Playwright locator with fallback chain."""
        # This is implemented in the replay engine
        pass


class ActionStep(BaseModel):
    """
    A single step in the automation flow.
    
    Each step is self-contained with its own targeting, verification,
    and error handling configuration.
    """
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    action: ActionType = Field(..., description="Action to perform")
    description: str = Field(..., description="Human-readable description")
    
    # Target element (for actions that need a target)
    target: Optional[ElementLocator] = Field(default=None, description="Element to act on")
    
    # Action-specific parameters
    value: Optional[str] = Field(default=None, description="Value for type/select actions")
    url: Optional[str] = Field(default=None, description="URL for navigate actions")
    key: Optional[str] = Field(default=None, description="Key for key_press actions")
    wait_for: Optional[str] = Field(default=None, description="Condition to wait for")
    wait_timeout_ms: int = Field(default=10000, description="Wait timeout in milliseconds")
    
    # Extraction configuration (for EXTRACT actions)
    extract_as: Optional[str] = Field(default=None, description="Output parameter name")
    extract_attribute: Optional[str] = Field(default=None, description="Attribute to extract (text, value, href, etc.)")
    extract_multiple: bool = Field(default=False, description="Extract multiple elements")
    
    # Checkpoint/verification
    checkpoint: Optional[str] = Field(default=None, description="Condition to verify after action")
    checkpoint_locator: Optional[ElementLocator] = Field(default=None, description="Element to verify")
    checkpoint_timeout_ms: int = Field(default=5000, description="Checkpoint verification timeout")
    
    # Error handling
    continue_on_failure: bool = Field(default=False, description="Continue flow if this step fails")
    expected_errors: List[str] = Field(
        default_factory=list,
        description="Known error patterns that are acceptable business outcomes"
    )
    retry_count: int = Field(default=0, description="Number of retries for this step")
    retry_delay_ms: int = Field(default=1000, description="Delay between retries")
    
    # Risk classification
    is_risky: bool = Field(default=False, description="Whether this action is risky/irreversible")
    requires_confirmation: bool = Field(default=False, description="Requires human confirmation")


class InputParameter(BaseModel):
    """Typed input parameter for the capability."""
    name: str = Field(..., description="Parameter name")
    type: Literal["string", "number", "boolean", "date", "enum"] = Field(..., description="Parameter type")
    description: str = Field(..., description="Parameter description")
    required: bool = Field(default=True, description="Whether parameter is required")
    default: Optional[Any] = Field(default=None, description="Default value")
    enum_values: Optional[List[str]] = Field(default=None, description="Allowed values for enum type")
    sensitive: bool = Field(default=False, description="Whether parameter contains sensitive data")
    validation_pattern: Optional[str] = Field(default=None, description="Regex validation pattern")


class OutputParameter(BaseModel):
    """Typed output parameter returned by the capability."""
    name: str = Field(..., description="Output parameter name")
    type: Literal["string", "number", "boolean", "object", "array"] = Field(..., description="Output type")
    description: str = Field(..., description="Output description")
    source_step: str = Field(..., description="Step ID that produces this output")
    extract_path: Optional[str] = Field(default=None, description="JSONPath to extract from step result")


class SuccessCondition(BaseModel):
    """Defines what constitutes successful completion."""
    type: Literal["checkpoint", "url_pattern", "element_present", "element_absent", "custom"] = Field(...)
    value: str = Field(..., description="Pattern/selector/condition value")
    description: str = Field(..., description="Human-readable success description")


class ErrorOutcome(BaseModel):
    """
    Defines a known error/business outcome that is not a failure.
    
    This is crucial: "no such member" is a valid business result, not a crash.
    """
    name: str = Field(..., description="Outcome identifier")
    description: str = Field(..., description="Human-readable description")
    detection: Dict[str, Any] = Field(..., description="How to detect this outcome")
    # Example: {"type": "element_text", "locator": {...}, "pattern": "Member not found"}
    is_recoverable: bool = Field(default=False, description="Can automation recover from this")
    recovery_steps: List[ActionStep] = Field(default_factory=list, description="Steps to recover")


class ArtifactMetadata(BaseModel):
    """Metadata for the artifact."""
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Capability name (e.g., 'lookup_member_savings_balance')")
    version: str = Field(default="1.0.0", description="Semantic version")
    description: str = Field(..., description="What this capability does")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(default="agent", description="Agent or human who created this")
    tags: List[str] = Field(default_factory=list, description="Searchable tags")
    target_application: str = Field(..., description="Target application identifier")
    target_url_pattern: str = Field(..., description="URL pattern this artifact works on")


class AutomationArtifact(BaseModel):
    """
    The core artifact schema - a reusable, versioned automation capability.
    
    This is what gets saved after a successful discovery run and what
    the replay engine executes deterministically.
    """
    metadata: ArtifactMetadata
    inputs: List[InputParameter] = Field(default_factory=list, description="Input parameters")
    outputs: List[OutputParameter] = Field(default_factory=list, description="Output parameters")
    steps: List[ActionStep] = Field(..., description="Ordered action steps")
    success_condition: SuccessCondition = Field(..., description="How to verify success")
    known_outcomes: List[ErrorOutcome] = Field(
        default_factory=list,
        description="Known business outcomes (not failures)"
    )
    preconditions: List[str] = Field(
        default_factory=list,
        description="Preconditions that must be met before execution"
    )
    postconditions: List[str] = Field(
        default_factory=list,
        description="Guaranteed state after successful execution"
    )
    
    # Safety
    allowed_domains: List[str] = Field(default_factory=list, description="Domains this artifact may access")
    risky_steps: List[str] = Field(default_factory=list, description="Step IDs that are risky")
    
    def get_step(self, step_id: str) -> Optional[ActionStep]:
        """Get a step by ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None
    
    def validate_inputs(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and coerce input parameters."""
        validated = {}
        for param in self.inputs:
            if param.name in inputs:
                validated[param.name] = inputs[param.name]
            elif param.required and param.default is None:
                raise ValueError(f"Required input '{param.name}' not provided")
            elif param.default is not None:
                validated[param.name] = param.default
        return validated
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return self.model_dump_json(indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> "AutomationArtifact":
        """Deserialize from JSON."""
        return cls.model_validate_json(json_str)
    
    def save(self, path: str) -> None:
        """Save artifact to file."""
        import json
        with open(path, 'w') as f:
            f.write(self.to_json())
    
    @classmethod
    def load(cls, path: str) -> "AutomationArtifact":
        """Load artifact from file."""
        import json
        with open(path, 'r') as f:
            return cls.from_json(f.read())


class ArtifactRunResult(BaseModel):
    """Result of executing an artifact (discovery or replay)."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artifact_id: Optional[str] = None
    artifact_version: Optional[str] = None
    mode: Literal["discovery", "replay"] = Field(...)
    status: Literal["success", "business_outcome", "failure", "escalated"] = Field(...)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    business_outcome: Optional[str] = Field(default=None, description="Known outcome name if applicable")
    error: Optional[str] = Field(default=None)
    error_details: Optional[Dict[str, Any]] = Field(default=None)
    steps_executed: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    
    def mark_completed(self, status: str, **kwargs):
        self.completed_at = datetime.utcnow()
        self.status = status
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)