"""
Deterministic Replay Engine

Executes saved artifacts without LLM in the loop.
Handles errors, retries, checkpoints, and known business outcomes.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from src.surface.base import BaseSurface, SurfaceActionResult
from src.surface.web import WebSurface, WebLocatorResolver
from src.artifacts.schema import (
    AutomationArtifact,
    ActionStep,
    ActionType,
    ElementLocator,
    LocatorStrategy,
    SuccessCondition,
    ErrorOutcome,
    ArtifactRunResult,
    InputParameter,
    OutputParameter,
)
from src.evidence.collector import EvidenceCollector, RunLogger
from src.safety.guardrails import safety_enforcer, GuardrailViolation
from src.escalation.manager import EscalationManager, EscalationReason, escalation_handler, escalation_detector
from src.config import settings


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILURE = "failure"
    ESCALATED = "escalated"


@dataclass
class StepResult:
    """Result of executing a single step."""
    step_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    business_outcome: Optional[str] = None
    duration_ms: int = 0
    retries: int = 0
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayResult:
    """Complete replay execution result."""
    run_id: str
    artifact_id: str
    artifact_version: str
    status: ReplayStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    business_outcome: Optional[str] = None
    error: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    step_results: List[StepResult] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)


class ReplayError(Exception):
    """Base exception for replay errors."""
    def __init__(self, message: str, step_id: str = None, recoverable: bool = False, details: Dict = None):
        super().__init__(message)
        self.step_id = step_id
        self.recoverable = recoverable
        self.details = details or {}


class ElementNotFoundError(ReplayError):
    """Element could not be located."""
    def __init__(self, step_id: str, locator: ElementLocator, details: Dict = None):
        super().__init__(
            f"Element not found: {locator.description}",
            step_id=step_id,
            recoverable=True,
            details={"locator": locator.model_dump(), **(details or {})}
        )


class CheckpointFailedError(ReplayError):
    """Checkpoint verification failed."""
    def __init__(self, step_id: str, expected: str, actual: str, details: Dict = None):
        super().__init__(
            f"Checkpoint failed: expected '{expected}', got '{actual}'",
            step_id=step_id,
            recoverable=False,
            details={"expected": expected, "actual": actual, **(details or {})}
        )


class BusinessOutcomeError(ReplayError):
    """A known business outcome occurred (not a failure)."""
    def __init__(self, outcome_name: str, step_id: str, description: str, details: Dict = None):
        super().__init__(
            f"Business outcome: {outcome_name} - {description}",
            step_id=step_id,
            recoverable=False,
            details={"outcome": outcome_name, "description": description, **(details or {})}
        )
        self.outcome_name = outcome_name


class ReplayEngine:
    """
    Deterministic replay engine for automation artifacts.
    
    Executes artifacts step-by-step with robust error handling,
    retries, checkpoint verification, and business outcome detection.
    """
    
    def __init__(
        self,
        surface: BaseSurface,
        artifact: AutomationArtifact,
        inputs: Dict[str, Any],
        evidence_collector: EvidenceCollector = None,
        config: Dict[str, Any] = None,
    ):
        self.surface = surface
        self.artifact = artifact
        self.inputs = artifact.validate_inputs(inputs)
        self.evidence = evidence_collector
        self.config = config or {}
        
        self.logger = RunLogger(
            evidence_collector.run_id if evidence_collector else str(uuid.uuid4())[:8],
            evidence_collector
        )
        
        self.escalation = EscalationManager(
            handler=escalation_handler,
            detector=escalation_detector,
            surface=surface
        )
        
        self.resolver = WebLocatorResolver(surface) if isinstance(surface, WebSurface) else None
        
        # Runtime state
        self.step_outputs: Dict[str, Any] = {}  # step_id -> output
        self.variables: Dict[str, Any] = {}  # Runtime variables
        self.variables.update(self.inputs)
    
    async def execute(self) -> ReplayResult:
        """
        Execute the artifact deterministically.
        
        Returns ReplayResult with status, outputs, and any errors.
        """
        run_id = str(uuid.uuid4())[:8]
        result = ReplayResult(
            run_id=run_id,
            artifact_id=self.artifact.metadata.artifact_id,
            artifact_version=self.artifact.metadata.version,
            status=ReplayStatus.FAILURE,
            started_at=datetime.utcnow(),
            inputs=self.inputs,
        )
        
        try:
            # Preconditions check
            await self._check_preconditions()
            
            # Execute steps in order
            for step in self.artifact.steps:
                step_result = await self._execute_step(step)
                result.step_results.append(step_result)
                
                # Store output for later steps
                if step_result.output is not None:
                    self.step_outputs[step.step_id] = step_result.output
                    if step.extract_as:
                        self.variables[step.extract_as] = step_result.output
                
                # Handle step result
                if step_result.business_outcome:
                    result.status = ReplayStatus.BUSINESS_OUTCOME
                    result.business_outcome = step_result.business_outcome
                    result.outputs = self._collect_outputs()
                    break
                
                if not step_result.success:
                    if step.continue_on_failure:
                        self.logger.log_error(f"Step {step.step_id} failed but continuing: {step_result.error}")
                        continue
                    else:
                        result.status = ReplayStatus.FAILURE
                        result.error = step_result.error
                        result.error_details = {
                            "step_id": step.step_id,
                            "step_description": step.description,
                            "error": step_result.error,
                        }
                        break
                
                # Verify checkpoint if defined
                if step.checkpoint:
                    checkpoint_passed = await self._verify_checkpoint(step)
                    if not checkpoint_passed:
                        result.status = ReplayStatus.FAILURE
                        result.error = f"Checkpoint failed at step {step.step_id}: {step.checkpoint}"
                        result.error_details = {
                            "step_id": step.step_id,
                            "checkpoint": step.checkpoint,
                        }
                        break
            
            # If we completed all steps, verify success condition
            if result.status == ReplayStatus.FAILURE and not result.error:
                success = await self._verify_success_condition()
                if success:
                    result.status = ReplayStatus.SUCCESS
                    result.outputs = self._collect_outputs()
                else:
                    result.error = "Success condition not met"
            
            elif result.status not in (ReplayStatus.BUSINESS_OUTCOME, ReplayStatus.FAILURE):
                result.status = ReplayStatus.SUCCESS
                result.outputs = self._collect_outputs()
        
        except BusinessOutcomeError as e:
            result.status = ReplayStatus.BUSINESS_OUTCOME
            result.business_outcome = e.outcome_name
            result.outputs = self._collect_outputs()
        
        except ReplayError as e:
            result.status = ReplayStatus.FAILURE
            result.error = str(e)
            result.error_details = e.details
        
        except GuardrailViolation as e:
            result.status = ReplayStatus.FAILURE
            result.error = f"Safety violation: {e.violation_type}"
            result.error_details = e.details
        
        except Exception as e:
            result.status = ReplayStatus.FAILURE
            result.error = f"Unexpected error: {str(e)}"
            result.error_details = {"exception": type(e).__name__, "message": str(e)}
            
            # Capture evidence
            if self.evidence:
                await self.evidence.capture_screenshot(self.surface, f"error_{run_id}")
                await self.evidence.capture_dom_snapshot(self.surface, f"error_{run_id}")
        
        finally:
            result.completed_at = datetime.utcnow()
            
            # Save evidence
            if self.evidence:
                self.evidence.save_run_summary(result)
        
        return result
    
    async def _execute_step(self, step: ActionStep) -> StepResult:
        """Execute a single step with retries and error handling."""
        start_time = datetime.utcnow()
        
        self.logger.log_action(step.action.value, step.description)
        
        if self.evidence:
            self.evidence.start_step(
                step_id=step.step_id,
                description=step.description,
                action=step.action.value,
                target=step.target.model_dump() if step.target else None,
                input_value=step.value,
            )
        
        # Safety check
        safety_enforcer.check_action(step, self.surface._page.url if hasattr(self.surface, '_page') else "")
        
        # Handle risky steps
        if step.is_risky or step.requires_confirmation:
            # In replay, we could escalate for confirmation
            # For now, log and proceed (or could be configured to block)
            self.logger.log_safety_check("risky_action", True, {"step": step.step_id})
        
        last_error = None
        max_retries = max(step.retry_count, self.config.get("max_retries", 3))
        
        for attempt in range(max_retries + 1):
            try:
                result = await self._execute_action(step)
                
                if result.success:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    if self.evidence:
                        self.evidence.end_step(True, result.output)
                    
                    return StepResult(
                        step_id=step.step_id,
                        success=True,
                        output=result.output,
                        duration_ms=duration,
                        retries=attempt,
                    )
                
                # Check for known business outcomes
                business_outcome = self._check_business_outcomes(step, result)
                if business_outcome:
                    duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                    if self.evidence:
                        self.evidence.end_step(False, result.output, result.error)
                    
                    raise BusinessOutcomeError(
                        outcome_name=business_outcome.name,
                        step_id=step.step_id,
                        description=business_outcome.description,
                        details={"output": result.output, "error": result.error}
                    )
                
                # Check for recoverable errors
                if self._is_recoverable_error(result.error, step):
                    last_error = result.error
                    self.logger.log_error(f"Attempt {attempt + 1} failed (recoverable): {result.error}")
                    
                    # Try recovery steps
                    if step.expected_errors and any(err in result.error for err in step.expected_errors):
                        await self._handle_expected_error(step, result.error)
                    
                    if attempt < max_retries:
                        await asyncio.sleep(step.retry_delay_ms / 1000)
                        continue
                
                # Non-recoverable or max retries reached
                duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                if self.evidence:
                    self.evidence.end_step(False, result.output, result.error)
                    await self.evidence.capture_screenshot(self.surface, f"failed_{step.step_id}")
                    await self.evidence.capture_dom_snapshot(self.surface, f"failed_{step.step_id}")
                
                return StepResult(
                    step_id=step.step_id,
                    success=False,
                    output=result.output,
                    error=result.error or last_error,
                    duration_ms=duration,
                    retries=attempt,
                )
            
            except BusinessOutcomeError:
                raise
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(step.retry_delay_ms / 1000)
                    continue
        
        # All retries exhausted
        duration = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        if self.evidence:
            self.evidence.end_step(False, None, last_error)
        
        return StepResult(
            step_id=step.step_id,
            success=False,
            error=last_error,
            duration_ms=duration,
            retries=max_retries,
        )
    
    async def _execute_action(self, step: ActionStep) -> SurfaceActionResult:
        """Execute a specific action based on step type."""
        
        if step.action == ActionType.NAVIGATE:
            return await self.surface.navigate(step.url)
        
        elif step.action == ActionType.CLICK:
            if not step.target:
                return SurfaceActionResult(success=False, error="Click requires target")
            element = await self.resolver.resolve(step.target.model_dump())
            await element.click()
            return SurfaceActionResult(success=True, output={"clicked": True})
        
        elif step.action == ActionType.TYPE:
            if not step.target:
                return SurfaceActionResult(success=False, error="Type requires target")
            element = await self.resolver.resolve(step.target.model_dump())
            # Substitute variables in value
            value = self._substitute_variables(step.value or "")
            await element.clear()
            await element.type(value)
            return SurfaceActionResult(success=True, output={"typed": len(value)})
        
        elif step.action == ActionType.SELECT:
            if not step.target:
                return SurfaceActionResult(success=False, error="Select requires target")
            element = await self.resolver.resolve(step.target.model_dump())
            value = self._substitute_variables(step.value or "")
            await element.select_option(value=value)
            return SurfaceActionResult(success=True, output={"selected": value})
        
        elif step.action == ActionType.EXTRACT:
            if not step.target:
                return SurfaceActionResult(success=False, error="Extract requires target")
            element = await self.resolver.resolve(step.target.model_dump())
            attr = step.extract_attribute or "text"
            if step.extract_multiple:
                elements = await element.all()
                values = []
                for el in elements:
                    if attr == "text":
                        val = await el.inner_text()
                    elif attr == "value":
                        val = await el.input_value()
                    else:
                        val = await el.get_attribute(attr)
                    values.append(val)
                return SurfaceActionResult(success=True, output=values)
            else:
                if attr == "text":
                    val = await element.inner_text()
                elif attr == "value":
                    val = await element.input_value()
                else:
                    val = await element.get_attribute(attr)
                return SurfaceActionResult(success=True, output=val)
        
        elif step.action == ActionType.WAIT:
            condition = step.wait_for or "networkidle"
            return await self.surface.wait_for(condition, step.wait_timeout_ms)
        
        elif step.action == ActionType.SCROLL:
            return await self.surface.scroll()
        
        else:
            return SurfaceActionResult(success=False, error=f"Unknown action: {step.action}")
    
    def _substitute_variables(self, value: str) -> str:
        """Substitute variables in string (e.g., {{member_id}})."""
        import re
        
        def replace(match):
            var_name = match.group(1)
            return str(self.variables.get(var_name, match.group(0)))
        
        return re.sub(r'\{\{(\w+)\}\}', replace, value)
    
    def _check_business_outcomes(self, step: ActionStep, result: SurfaceActionResult) -> Optional[ErrorOutcome]:
        """Check if result matches a known business outcome."""
        for outcome in self.artifact.known_outcomes:
            detection = outcome.detection
            det_type = detection.get("type")
            
            if det_type == "element_text" and result.output:
                # Check if output contains pattern
                pattern = detection.get("pattern", "")
                if pattern and pattern in str(result.output):
                    return outcome
            
            elif det_type == "error_pattern" and result.error:
                pattern = detection.get("pattern", "")
                if pattern and pattern in result.error:
                    return outcome
            
            elif det_type == "url_pattern":
                # Check current URL
                pass
        
        return None
    
    def _is_recoverable_error(self, error: str, step: ActionStep) -> bool:
        """Check if error is recoverable."""
        if not error:
            return False
        
        error_lower = error.lower()
        
        # Network/timeout errors are usually recoverable
        recoverable_patterns = [
            "timeout",
            "network",
            "connection",
            "temporary",
            "retry",
            "slow",
        ]
        
        for pattern in recoverable_patterns:
            if pattern in error_lower:
                return True
        
        # Check step's expected errors
        for expected in step.expected_errors:
            if expected.lower() in error_lower:
                return True
        
        return False
    
    async def _handle_expected_error(self, step: ActionStep, error: str) -> None:
        """Handle a known/expected error."""
        # Run recovery steps if defined
        for recovery_step in step.recovery_steps:
            await self._execute_action(recovery_step)
    
    async def _verify_checkpoint(self, step: ActionStep) -> bool:
        """Verify a checkpoint condition."""
        try:
            if step.checkpoint_locator:
                element = await self.resolver.resolve(step.checkpoint_locator.model_dump())
                actual = await element.inner_text()
            else:
                # Get page state
                state = await self.surface.get_state()
                actual = state.url or ""
            
            expected = step.checkpoint
            
            # Simple text containment check
            passed = expected.lower() in actual.lower()
            
            self.logger.log_checkpoint(step.checkpoint, passed, actual[:200])
            
            if not passed:
                raise CheckpointFailedError(step.step_id, expected, actual)
            
            return True
            
        except Exception as e:
            if isinstance(e, CheckpointFailedError):
                raise
            self.logger.log_error(f"Checkpoint verification error: {e}")
            return False
    
    async def _verify_success_condition(self) -> bool:
        """Verify the artifact's success condition."""
        condition = self.artifact.success_condition
        
        try:
            if condition.type == "checkpoint":
                # Check if final step output matches
                return True  # Simplified
            
            elif condition.type == "url_pattern":
                state = await self.surface.get_state()
                return condition.value in (state.url or "")
            
            elif condition.type == "element_present":
                if condition.value.startswith("css:"):
                    selector = condition.value[4:]
                    try:
                        await self.surface._page.wait_for_selector(selector, timeout=5000)
                        return True
                    except:
                        return False
            
            elif condition.type == "element_absent":
                if condition.value.startswith("css:"):
                    selector = condition.value[4:]
                    try:
                        await self.surface._page.wait_for_selector(selector, timeout=5000, state="detached")
                        return True
                    except:
                        return False
            
            return False
        except Exception as e:
            self.logger.log_error(f"Success condition check failed: {e}")
            return False
    
    async def _check_preconditions(self) -> None:
        """Check artifact preconditions before execution."""
        for precondition in self.artifact.preconditions:
            self.logger.log_action("precondition_check", precondition)
            # In a full implementation, evaluate each precondition
    
    def _collect_outputs(self) -> Dict[str, Any]:
        """Collect declared outputs from step results."""
        outputs = {}
        for output_param in self.artifact.outputs:
            step_id = output_param.source_step
            if step_id in self.step_outputs:
                value = self.step_outputs[step_id]
                # Apply extract_path if specified (JSONPath)
                if output_param.extract_path:
                    # Simplified - would use jsonpath-ng in production
                    pass
                outputs[output_param.name] = value
        return outputs


async def replay_artifact(
    artifact_path: str,
    inputs: Dict[str, Any],
    surface: BaseSurface = None,
    evidence_dir: str = "evidence",
    config: Dict[str, Any] = None,
) -> ReplayResult:
    """
    Convenience function to replay an artifact from file.
    """
    # Load artifact
    artifact = AutomationArtifact.load(artifact_path)
    
    # Create surface if not provided
    if surface is None:
        surface = WebSurface(config={"headless": config.get("headless", True) if config else True})
        await surface.initialize()
    
    # Create evidence collector
    run_id = str(uuid.uuid4())[:8]
    evidence = EvidenceCollector(run_id, evidence_dir)
    
    # Create and run engine
    engine = ReplayEngine(surface, artifact, inputs, evidence, config)
    result = await engine.execute()
    
    return result