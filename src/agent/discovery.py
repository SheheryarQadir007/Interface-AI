"""
Agent Loop for Discovery Phase

The LLM-driven observe-decide-act loop that discovers how to accomplish a goal
against a live application surface.
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field

from src.agent.llm_client import LLMClient, LLMMessage, create_llm_client
from src.surface.base import BaseSurface, SurfaceState, SurfaceActionResult
from src.surface.web import WebSurface
from src.artifacts.schema import (
    AutomationArtifact,
    ActionStep,
    ActionType,
    ElementLocator,
    LocatorStrategy,
    InputParameter,
    OutputParameter,
    SuccessCondition,
    ArtifactMetadata,
    ArtifactRunResult,
)
from src.evidence.collector import EvidenceCollector, RunLogger, setup_structured_logging
from src.safety.guardrails import safety_enforcer, GuardrailViolation
from src.escalation.manager import EscalationManager, EscalationReason, escalation_handler, escalation_detector
from src.config import settings


@dataclass
class AgentConfig:
    """Configuration for the agent loop."""
    max_steps: int = 20
    step_timeout_seconds: int = 30
    overall_timeout_seconds: int = 300
    llm_provider: str = "mock"
    llm_model: str = None
    capture_screenshots: bool = True
    capture_dom: bool = True
    headless: bool = False


class AgentAction:
    """Represents an action the agent can take."""
    
    # Tool definitions for LLM function calling
    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "navigate",
                "description": "Navigate to a URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to navigate to"},
                        "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"], "default": "networkidle"},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "click",
                "description": "Click an element",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector or text to click"},
                        "strategy": {"type": "string", "enum": ["css", "text", "role", "testid"], "default": "css"},
                        "force": {"type": "boolean", "default": False},
                    },
                    "required": ["selector"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "type_text",
                "description": "Type text into an input field",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector for the input"},
                        "text": {"type": "string", "description": "Text to type"},
                        "clear_first": {"type": "boolean", "default": True},
                    },
                    "required": ["selector", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "select_option",
                "description": "Select an option from a dropdown",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector for the select element"},
                        "value": {"type": "string", "description": "Value to select"},
                    },
                    "required": ["selector", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_text",
                "description": "Extract text from an element",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector for the element"},
                        "attribute": {"type": "string", "default": "text", "description": "Attribute to extract"},
                    },
                    "required": ["selector"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wait_for",
                "description": "Wait for a condition",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "condition": {"type": "string", "description": "Condition to wait for (url:, element:, networkidle)"},
                        "timeout_ms": {"type": "integer", "default": 10000},
                    },
                    "required": ["condition"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scroll",
                "description": "Scroll the page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "default": "down"},
                        "amount": {"type": "integer", "default": 500},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_page_state",
                "description": "Get current page state (URL, title, visible elements)",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


class DiscoveryAgent:
    """
    LLM-driven agent that discovers how to accomplish a goal.
    
    Runs the observe-decide-act loop against a live surface,
    building up an artifact that can be replayed deterministically.
    """
    
    def __init__(
        self,
        surface: BaseSurface,
        config: AgentConfig = None,
        evidence_collector: EvidenceCollector = None,
    ):
        self.surface = surface
        self.config = config or AgentConfig()
        self.evidence = evidence_collector
        self.logger = RunLogger(
            evidence_collector.run_id if evidence_collector else str(uuid.uuid4())[:8],
            evidence_collector
        )
        
        # LLM client
        self.llm = create_llm_client(self.config.llm_provider)
        
        # Escalation
        self.escalation = EscalationManager(
            handler=escalation_handler,
            detector=escalation_detector,
            surface=surface
        )
        
        # State
        self.steps_taken: List[ActionStep] = []
        self.step_count = 0
        self.goal_achieved = False
        self.goal = ""
        self.inputs: Dict[str, Any] = {}
        self.outputs: Dict[str, Any] = {}
        self.start_time = datetime.utcnow()
    
    async def run(self, goal: str, inputs: Dict[str, Any] = None) -> ArtifactRunResult:
        """
        Run the discovery loop to accomplish a goal.
        
        Returns ArtifactRunResult with the discovered artifact if successful.
        """
        self.goal = goal
        self.inputs = inputs or {}
        self.start_time = datetime.utcnow()
        
        run_result = ArtifactRunResult(
            mode="discovery",
            status="failure",
            inputs=self.inputs,
        )
        
        try:
            # Initial observation
            state = await self.surface.get_state()
            await self._log_observation(state)
            
            # Main loop
            while self.step_count < self.config.max_steps:
                # Check overall timeout
                if (datetime.utcnow() - self.start_time).total_seconds() > self.config.overall_timeout_seconds:
                    run_result.mark_completed("failure", error="Overall timeout exceeded")
                    break
                
                # Check for escalation conditions
                esc_reason = self.escalation.detector.check_stuck()
                if esc_reason:
                    esc_result = await self.escalation.maybe_escalate(
                        reason=esc_reason,
                        description=f"Agent stuck after {self.step_count} steps",
                        artifact_name="discovery",
                        current_step=self.steps_taken[-1] if self.steps_taken else None,
                        current_url=state.url,
                    )
                    if esc_result and esc_result.status == EscalationStatus.RESOLVED:
                        # Continue after human intervention
                        state = await self.surface.get_state()
                        continue
                    else:
                        run_result.mark_completed("escalated", error="Escalation failed or timed out")
                        break
                
                # Get LLM decision
                action = await self._decide_action(state)
                
                if not action:
                    run_result.mark_completed("failure", error="LLM returned no action")
                    break
                
                # Execute action
                result = await self._execute_action(action, state)
                
                # Record step
                step = self._create_step_record(action, result)
                self.steps_taken.append(step)
                self.step_count += 1
                
                # Update evidence
                if self.evidence:
                    self.evidence.end_step(result.success, result.output, result.error)
                
                # Check for goal completion
                if self._check_goal_complete(state, result):
                    self.goal_achieved = True
                    # Build artifact from successful steps
                    artifact = self._build_artifact()
                    run_result.mark_completed(
                        "success",
                        artifact_id=artifact.metadata.artifact_id,
                        artifact_version=artifact.metadata.version,
                        outputs=self.outputs,
                    )
                    run_result.artifact_id = artifact.metadata.artifact_id
                    run_result.artifact_version = artifact.metadata.version
                    break
                
                # Update state
                if result.new_state:
                    state = result.new_state
                else:
                    state = await self.surface.get_state()
                
                await self._log_observation(state)
                
                # Small delay between steps
                await asyncio.sleep(0.5)
            
            if not self.goal_achieved:
                run_result.mark_completed("failure", error=f"Max steps ({self.config.max_steps}) reached")
        
        except Exception as e:
            run_result.mark_completed("failure", error=str(e))
            self.logger.log_error(str(e))
        
        # Save evidence
        if self.evidence:
            self.evidence.save_run_summary(run_result)
        
        return run_result
    
    async def _decide_action(self, state: SurfaceState) -> Optional[Dict[str, Any]]:
        """Ask LLM what to do next."""
        
        # Build observation for LLM
        observation = self._build_observation(state)
        
        # System prompt
        system_prompt = """You are an AI agent operating a banking application to accomplish a goal.
You have access to tools for navigation, clicking, typing, selecting, extracting text, waiting, and scrolling.

Your task: """ + self.goal + """

Available inputs: """ + json.dumps(self.inputs) + """

Current page state:
- URL: """ + (state.url or "unknown") + """
- Title: """ + (state.title or "unknown") + """

Visible elements (sample):
""" + self._format_elements(state.elements[:50]) + """

Rules:
1. Use tools to interact with the page
2. Be specific with selectors - prefer data-testid, aria-label, or role+name
3. Verify your actions worked before proceeding
4. Extract data when needed for the goal
5. If stuck, explain why and what you'd try next
6. Never enter real credentials or PII - use test data only

Think step by step and use the tools to accomplish the goal."""
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=observation),
        ]
        
        self.logger.log_agent_thought(
            thought="Deciding next action based on current page state",
            observation=observation[:500],
        )
        
        response = await self.llm.complete(
            messages=messages,
            tools=AgentAction.TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )
        
        # Parse tool calls
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            return {
                "tool": tool_call["name"],
                "arguments": tool_call["arguments"],
                "reasoning": response.content,
            }
        
        # If no tool call, treat content as reasoning and try to infer action
        return {"reasoning": response.content, "tool": None}
    
    def _build_observation(self, state: SurfaceState) -> str:
        """Build observation text for LLM."""
        obs = f"Current URL: {state.url}\n"
        obs += f"Page Title: {state.title}\n\n"
        obs += "Visible interactive elements:\n"
        
        for el in state.elements[:30]:
            if el.tag_name in ["a", "button", "input", "select", "textarea", "form"]:
                hint = ""
                if el.selector_hints:
                    if el.selector_hints.get("testId"):
                        hint = f" [data-testid={el.selector_hints['testId']}]"
                    elif el.selector_hints.get("css"):
                        hint = f" [{el.selector_hints['css']}]"
                
                text_preview = (el.text or "")[:100]
                obs += f"  - <{el.tag_name}>{hint} role={el.role} name={el.name} text=\"{text_preview}\"\n"
        
        return obs
    
    def _format_elements(self, elements: List) -> str:
        """Format elements for LLM prompt."""
        lines = []
        for el in elements:
            if el.tag_name in ["a", "button", "input", "select", "textarea", "form", "table", "td", "th"]:
                text = (el.text or "")[:80]
                attrs = []
                if el.attributes.get("id"): attrs.append(f"id={el.attributes['id']}")
                if el.attributes.get("class"): attrs.append(f"class={el.attributes['class']}")
                if el.attributes.get("data-testid"): attrs.append(f"data-testid={el.attributes['data-testid']}")
                if el.attributes.get("aria-label"): attrs.append(f"aria-label={el.attributes['aria-label']}")
                if el.attributes.get("role"): attrs.append(f"role={el.attributes['role']}")
                if el.attributes.get("name"): attrs.append(f"name={el.attributes['name']}")
                if el.attributes.get("type"): attrs.append(f"type={el.attributes['type']}")
                if el.attributes.get("placeholder"): attrs.append(f"placeholder={el.attributes['placeholder']}")
                
                lines.append(f"  {el.tag_name} {' '.join(attrs)} | \"{text}\"")
        return "\n".join(lines)
    
    async def _execute_action(self, action: Dict[str, Any], state: SurfaceState) -> SurfaceActionResult:
        """Execute an action on the surface."""
        tool_name = action.get("tool")
        args = action.get("arguments", {})
        
        self.logger.log_action(tool_name, args)
        
        # Start evidence collection
        if self.evidence:
            self.evidence.start_step(
                step_id=f"step_{self.step_count}",
                description=action.get("reasoning", tool_name),
                action=tool_name,
                target=args,
                input_value=args.get("text") or args.get("url"),
            )
        
        try:
            # Safety check
            if tool_name == "navigate":
                safety_enforcer.check_navigation(args["url"])
            
            # Execute
            if tool_name == "navigate":
                result = await self.surface.navigate(args["url"], args.get("wait_until", "networkidle"))
            elif tool_name == "click":
                result = await self._execute_click(args)
            elif tool_name == "type_text":
                result = await self._execute_type(args)
            elif tool_name == "select_option":
                result = await self._execute_select(args)
            elif tool_name == "extract_text":
                result = await self._execute_extract(args)
            elif tool_name == "wait_for":
                result = await self.surface.wait_for(args["condition"], args.get("timeout_ms", 10000))
            elif tool_name == "scroll":
                result = await self.surface.scroll(args.get("direction", "down"), args.get("amount", 500))
            elif tool_name == "get_page_state":
                new_state = await self.surface.get_state()
                result = SurfaceActionResult(success=True, output=new_state, new_state=new_state)
            else:
                result = SurfaceActionResult(success=False, error=f"Unknown tool: {tool_name}")
            
            # Capture evidence on failure
            if not result.success and self.evidence:
                await self.evidence.capture_screenshot(self.surface, f"failed_{tool_name}")
                await self.evidence.capture_dom_snapshot(self.surface, f"failed_{tool_name}")
            
            self.logger.log_result(result.success, result.output, result.error)
            return result
            
        except GuardrailViolation as e:
            self.logger.log_safety_check(e.violation_type, False, e.details)
            return SurfaceActionResult(success=False, error=f"Safety violation: {e}")
        except Exception as e:
            self.logger.log_error(str(e))
            if self.evidence:
                await self.evidence.capture_screenshot(self.surface, f"error_{tool_name}")
                await self.evidence.capture_dom_snapshot(self.surface, f"error_{tool_name}")
            return SurfaceActionResult(success=False, error=str(e))
    
    async def _execute_click(self, args: Dict) -> SurfaceActionResult:
        """Execute click with multiple selector strategies."""
        selector = args["selector"]
        strategy = args.get("strategy", "css")
        
        # Build locator
        if strategy == "testid":
            locator = {"primary": "test_id", "primary_value": selector}
        elif strategy == "role":
            # Expect format: "role::name"
            if "::" in selector:
                locator = {"primary": "role_and_name", "primary_value": selector}
            else:
                locator = {"primary": "role_and_name", "primary_value": f"button::{selector}"}
        elif strategy == "text":
            locator = {"primary": "text_content", "primary_value": selector}
        else:
            locator = {"primary": "css_selector", "primary_value": selector}
        
        return await self.surface.click(locator, force=args.get("force", False))
    
    async def _execute_type(self, args: Dict) -> SurfaceActionResult:
        """Execute type text."""
        selector = args["selector"]
        text = args["text"]
        
        locator = {"primary": "css_selector", "primary_value": selector}
        return await self.surface.type_text(locator, text, clear_first=args.get("clear_first", True))
    
    async def _execute_select(self, args: Dict) -> SurfaceActionResult:
        """Execute select option."""
        selector = args["selector"]
        value = args["value"]
        
        locator = {"primary": "css_selector", "primary_value": selector}
        return await self.surface.select_option(locator, value)
    
    async def _execute_extract(self, args: Dict) -> SurfaceActionResult:
        """Execute extract text."""
        selector = args["selector"]
        attribute = args.get("attribute", "text")
        
        locator = {"primary": "css_selector", "primary_value": selector}
        return await self.surface.extract(locator, attribute=attribute)
    
    def _create_step_record(self, action: Dict, result: SurfaceActionResult) -> ActionStep:
        """Create an ActionStep record from executed action."""
        tool = action.get("tool")
        args = action.get("arguments", {})
        
        # Map tool to action type
        action_type_map = {
            "navigate": ActionType.NAVIGATE,
            "click": ActionType.CLICK,
            "type_text": ActionType.TYPE,
            "select_option": ActionType.SELECT,
            "extract_text": ActionType.EXTRACT,
            "wait_for": ActionType.WAIT,
            "scroll": ActionType.SCROLL,
        }
        
        action_type = action_type_map.get(tool, ActionType.CLICK)
        
        # Build target locator
        target = None
        if tool in ["click", "type_text", "select_option", "extract_text"]:
            selector = args.get("selector", "")
            strategy = args.get("strategy", "css")
            
            if strategy == "testid":
                primary = LocatorStrategy.TEST_ID
                primary_value = selector
            elif strategy == "role":
                primary = LocatorStrategy.ROLE_AND_NAME
                primary_value = selector
            elif strategy == "text":
                primary = LocatorStrategy.TEXT_CONTENT
                primary_value = selector
            else:
                primary = LocatorStrategy.CSS_SELECTOR
                primary_value = selector
            
            target = ElementLocator(
                primary=primary,
                primary_value=primary_value,
                description=f"{tool} on {selector}",
            )
        
        step = ActionStep(
            step_id=f"step_{self.step_count}",
            action=action_type,
            description=action.get("reasoning", tool),
            target=target,
            value=args.get("text") or args.get("url") or args.get("value"),
            url=args.get("url"),
            wait_for=args.get("condition"),
            extract_as=args.get("extract_as"),
            extract_attribute=args.get("attribute"),
            checkpoint=None,  # Will be set during artifact building
        )
        
        return step
    
    def _check_goal_complete(self, state: SurfaceState, result: SurfaceActionResult) -> bool:
        """Check if the goal has been achieved."""
        # This is goal-specific - in a real implementation, you'd have
        # goal-specific success criteria. For now, use a simple heuristic.
        
        # If we extracted data and it looks like a balance/account info
        if result.success and result.output:
            output_str = str(result.output).lower()
            if any(kw in output_str for kw in ["balance", "account", "member", "confirmation", "success"]):
                # Check if we have outputs captured
                if self.outputs:
                    return True
        
        return False
    
    def _build_artifact(self) -> AutomationArtifact:
        """Build artifact from successful discovery run."""
        # Determine inputs/outputs from the run
        inputs = [
            InputParameter(
                name=k,
                type="string",
                description=f"Input parameter: {k}",
                required=True,
            )
            for k in self.inputs.keys()
        ]
        
        outputs = [
            OutputParameter(
                name=k,
                type="string",
                description=f"Output: {k}",
                source_step="step_0",  # Simplified
            )
            for k in self.outputs.keys()
        ]
        
        # Add success condition
        success_condition = SuccessCondition(
            type="checkpoint",
            value="goal_achieved",
            description="Goal completed successfully",
        )
        
        metadata = ArtifactMetadata(
            name=self._generate_artifact_name(),
            version="1.0.0",
            description=f"Automated capability for: {self.goal}",
            target_application="demo_banking",
            target_url_pattern=settings.demo_app_url + "/*",
            tags=["discovery", "auto-generated"],
        )
        
        artifact = AutomationArtifact(
            metadata=metadata,
            inputs=inputs,
            outputs=outputs,
            steps=self.steps_taken,
            success_condition=success_condition,
        )
        
        return artifact
    
    def _generate_artifact_name(self) -> str:
        """Generate artifact name from goal."""
        # Simple slug generation
        import re
        name = re.sub(r'[^a-zA-Z0-9]+', '_', self.goal.lower())
        name = re.sub(r'_+', '_', name).strip('_')
        return name[:50] or "discovered_capability"
    
    async def _log_observation(self, state: SurfaceState) -> None:
        """Log observation for evidence."""
        if self.evidence:
            await self.evidence.capture_screenshot(self.surface, f"step_{self.step_count}")
            await self.evidence.capture_dom_snapshot(self.surface, f"step_{self.step_count}")