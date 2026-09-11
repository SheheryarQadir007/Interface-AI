"""
Human-in-the-Loop Escalation Module

Implements detection of stuck/blocked states, routing intervention requests,
and control transfer between automation and human operators.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import json


class EscalationReason(str, Enum):
    """Reasons for escalation."""
    STUCK_NO_ACTION = "stuck_no_action"           # Agent can't determine next action
    STUCK_ELEMENT_NOT_FOUND = "stuck_element_not_found"  # Can't locate required element
    STUCK_UNEXPECTED_STATE = "stuck_unexpected_state"    # UI state doesn't match expectation
    ERROR_UNRECOVERABLE = "error_unrecoverable"   # Hit an error that can't be recovered
    RISKY_ACTION = "risky_action"                 # Risky action needs human approval
    CONFIRMATION_REQUIRED = "confirmation_required"  # Explicit confirmation needed
    POLICY_VIOLATION = "policy_violation"         # Action blocked by policy
    SESSION_EXPIRED = "session_expired"           # Session timed out
    CAPTCHA_CHALLENGE = "captcha_challenge"       # CAPTCHA or bot detection
    UNKNOWN = "unknown"


class EscalationStatus(str, Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class EscalationContext:
    """Context passed to human operator for intervention."""
    escalation_id: str
    reason: EscalationReason
    description: str
    artifact_name: Optional[str] = None
    artifact_version: Optional[str] = None
    current_step_id: Optional[str] = None
    step_description: Optional[str] = None
    current_url: Optional[str] = None
    screenshot: Optional[bytes] = None
    dom_snapshot: Optional[str] = None
    accessibility_tree: Optional[Dict[str, Any]] = None
    error_details: Optional[Dict[str, Any]] = None
    session_context: Optional[Dict[str, Any]] = None  # For session handoff
    inputs: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EscalationResult:
    """Result of human intervention."""
    escalation_id: str
    status: EscalationStatus
    action_taken: str  # Description of what human did
    steps_performed: List[Dict[str, Any]] = field(default_factory=list)
    resumed_at_step: Optional[str] = None
    new_session_context: Optional[Dict[str, Any]] = None
    resolved_at: Optional[datetime] = None
    operator_id: Optional[str] = None


class EscalationHandler(ABC):
    """Abstract handler for escalation routing."""
    
    @abstractmethod
    async def request_intervention(self, context: EscalationContext) -> EscalationResult:
        """Request human intervention and return result."""
        pass
    
    @abstractmethod
    async def notify(self, context: EscalationContext) -> None:
        """Send notification about escalation."""
        pass


class MockOperatorConsole(EscalationHandler):
    """
    Mock operator console for demonstration.
    
    In production, this would integrate with a real operator UI,
    ticketing system (PagerDuty, Jira, etc.), or messaging platform.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.pending_escalations: Dict[str, EscalationContext] = {}
        self.results: Dict[str, EscalationResult] = {}
        self._response_queue: asyncio.Queue = asyncio.Queue()
    
    async def request_intervention(self, context: EscalationContext) -> EscalationResult:
        """Queue escalation and wait for operator response."""
        self.pending_escalations[context.escalation_id] = context
        
        # Notify (in real system, this would page/alert an operator)
        await self.notify(context)
        
        # Wait for response with timeout
        timeout = self.config.get("escalation_timeout", 300)
        try:
            result = await asyncio.wait_for(
                self._wait_for_response(context.escalation_id),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            # Escalation timed out
            result = EscalationResult(
                escalation_id=context.escalation_id,
                status=EscalationStatus.TIMED_OUT,
                action_taken="Escalation timed out - no operator response",
                resolved_at=datetime.utcnow()
            )
            self.results[context.escalation_id] = result
            return result
    
    async def _wait_for_response(self, escalation_id: str) -> EscalationResult:
        """Wait for operator response from queue."""
        while True:
            result = await self._response_queue.get()
            if result.escalation_id == escalation_id:
                return result
            # Put back if not ours
            await self._response_queue.put(result)
    
    async def notify(self, context: EscalationContext) -> None:
        """Send notification (mock - prints to console)."""
        print("\n" + "="*60)
        print("🚨 ESCALATION REQUESTED")
        print("="*60)
        print(f"ID: {context.escalation_id}")
        print(f"Reason: {context.reason.value}")
        print(f"Description: {context.description}")
        print(f"Artifact: {context.artifact_name} v{context.artifact_version}")
        print(f"Current Step: {context.current_step_id} - {context.step_description}")
        print(f"URL: {context.current_url}")
        print(f"Inputs: {context.inputs}")
        if context.error_details:
            print(f"Error: {context.error_details}")
        print("="*60)
        print("Waiting for operator response...")
        print("In production, this would notify a real operator console.")
        print("For demo, use `escalation_handler.simulate_operator_response(...)`")
        print("="*60 + "\n")
    
    def simulate_operator_response(
        self,
        escalation_id: str,
        action_taken: str,
        steps_performed: List[Dict[str, Any]] = None,
        resumed_at_step: str = None,
        new_session_context: Dict[str, Any] = None,
        operator_id: str = "demo_operator"
    ) -> None:
        """Simulate an operator response (for testing/demo)."""
        context = self.pending_escalations.get(escalation_id)
        if not context:
            raise ValueError(f"Escalation {escalation_id} not found")
        
        result = EscalationResult(
            escalation_id=escalation_id,
            status=EscalationStatus.RESOLVED,
            action_taken=action_taken,
            steps_performed=steps_performed or [],
            resumed_at_step=resumed_at_step,
            new_session_context=new_session_context,
            resolved_at=datetime.utcnow(),
            operator_id=operator_id
        )
        
        self.results[escalation_id] = result
        self._response_queue.put_nowait(result)
        
        print(f"\n✅ Operator {operator_id} responded to {escalation_id}")
        print(f"Action: {action_taken}")
        if steps_performed:
            print(f"Steps performed: {len(steps_performed)}")


class EscalationDetector:
    """
    Detects conditions that require human escalation.
    
    Runs alongside the agent/replay to identify stuck states.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.max_consecutive_failures = self.config.get("max_consecutive_failures", 3)
        self.stuck_timeout_seconds = self.config.get("stuck_timeout_seconds", 60)
        self.consecutive_failures = 0
        self.last_success_time = datetime.utcnow()
        self.last_action_time = datetime.utcnow()
    
    def record_success(self) -> None:
        """Record a successful action."""
        self.consecutive_failures = 0
        self.last_success_time = datetime.utcnow()
        self.last_action_time = datetime.utcnow()
    
    def record_failure(self, error: str, step_id: str = None) -> None:
        """Record a failed action."""
        self.consecutive_failures += 1
        self.last_action_time = datetime.utcnow()
    
    def record_action(self) -> None:
        """Record any action attempt."""
        self.last_action_time = datetime.utcnow()
    
    def check_stuck(self, current_step: Any = None) -> Optional[EscalationReason]:
        """
        Check if the automation is stuck.
        
        Returns escalation reason if stuck, None otherwise.
        """
        now = datetime.utcnow()
        
        # Check consecutive failures
        if self.consecutive_failures >= self.max_consecutive_failures:
            return EscalationReason.STUCK_NO_ACTION
        
        # Check stuck timeout (no progress for too long)
        if (now - self.last_success_time).total_seconds() > self.stuck_timeout_seconds:
            return EscalationReason.STUCK_NO_ACTION
        
        # Check if no action for too long
        if (now - self.last_action_time).total_seconds() > self.stuck_timeout_seconds:
            return EscalationReason.STUCK_NO_ACTION
        
        return None
    
    def check_element_not_found(self, step_id: str, attempts: int) -> Optional[EscalationReason]:
        """Check if element location has failed repeatedly."""
        if attempts >= 3:
            return EscalationReason.STUCK_ELEMENT_NOT_FOUND
        return None
    
    def check_unexpected_state(self, expected: str, actual: str) -> Optional[EscalationReason]:
        """Check if UI state doesn't match expectation."""
        # Simple heuristic - in production, use more sophisticated comparison
        if expected and actual and expected.lower() not in actual.lower():
            return EscalationReason.STUCK_UNEXPECTED_STATE
        return None
    
    def check_session_expired(self, session_context: Dict[str, Any]) -> Optional[EscalationReason]:
        """Check if session has expired."""
        # Look for login redirects, session timeout messages, etc.
        if session_context.get("redirected_to_login"):
            return EscalationReason.SESSION_EXPIRED
        return None


class EscalationManager:
    """
    Main escalation management class.
    
    Coordinates detection, routing, and handoff between automation and human.
    """
    
    def __init__(
        self,
        handler: EscalationHandler,
        detector: EscalationDetector = None,
        surface = None
    ):
        self.handler = handler
        self.detector = detector or EscalationDetector()
        self.surface = surface
        self.active_escalation: Optional[EscalationContext] = None
        self.escalation_history: List[Dict[str, Any]] = []
    
    async def maybe_escalate(
        self,
        reason: EscalationReason,
        description: str,
        artifact_name: str = None,
        artifact_version: str = None,
        current_step: Any = None,
        current_url: str = None,
        error_details: Dict[str, Any] = None,
        inputs: Dict[str, Any] = None
    ) -> Optional[EscalationResult]:
        """
        Check if escalation is needed and execute if so.
        
        Returns EscalationResult if escalated, None otherwise.
        """
        # Create context
        context = EscalationContext(
            escalation_id=str(uuid.uuid4())[:8],
            reason=reason,
            description=description,
            artifact_name=artifact_name,
            artifact_version=artifact_version,
            current_step_id=current_step.step_id if current_step else None,
            step_description=current_step.description if current_step else None,
            current_url=current_url,
            error_details=error_details,
            inputs=inputs or {},
        )
        
        # Capture current surface state for handoff
        if self.surface:
            context.screenshot = await self.surface.take_screenshot()
            context.accessibility_tree = await self.surface.get_accessibility_tree()
            context.session_context = await self.surface.pause_automation()
        
        self.active_escalation = context
        
        # Request intervention
        result = await self.handler.request_intervention(context)
        
        # Record in history
        self.escalation_history.append({
            "escalation_id": context.escalation_id,
            "reason": reason.value,
            "description": description,
            "result_status": result.status.value,
            "action_taken": result.action_taken,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Resume automation if resolved
        if result.status == EscalationStatus.RESOLVED and self.surface:
            if result.new_session_context:
                await self.surface.resume_automation(result.new_session_context)
            else:
                await self.surface.resume_automation(context.session_context)
        
        self.active_escalation = None
        return result
    
    def check_and_escalate_sync(
        self,
        current_step: Any = None,
        current_url: str = None,
        error_details: Dict[str, Any] = None
    ) -> Optional[EscalationReason]:
        """Synchronous check for escalation conditions."""
        reason = self.detector.check_stuck(current_step)
        if reason:
            return reason
        
        if error_details:
            if "element not found" in str(error_details).lower():
                return EscalationReason.STUCK_ELEMENT_NOT_FOUND
            if "session" in str(error_details).lower() and "expired" in str(error_details).lower():
                return EscalationReason.SESSION_EXPIRED
        
        return None
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Get escalation history."""
        return self.escalation_history.copy()


# Global instances (can be overridden per run)
escalation_handler = MockOperatorConsole()
escalation_detector = EscalationDetector()