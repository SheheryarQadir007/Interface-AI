"""
Evidence and Observability Module

Provides structured logging, screenshot capture, DOM snapshots,
and trace collection for debugging and audit trails.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
import structlog

from src.config import settings
from src.safety.guardrails import safety_enforcer


class EvidenceType(str, Enum):
    SCREENSHOT = "screenshot"
    DOM_SNAPSHOT = "dom_snapshot"
    ACCESSIBILITY_TREE = "accessibility_tree"
    TRACE = "trace"
    LOG = "log"
    VIDEO = "video"


@dataclass
class EvidenceRecord:
    """A single piece of evidence."""
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    run_id: str = ""
    step_id: Optional[str] = None
    type: EvidenceType = EvidenceType.LOG
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["type"] = self.type.value
        # Don't include raw binary data in dict
        if isinstance(self.data, bytes):
            d["data"] = f"<binary: {len(self.data)} bytes>"
        return d


@dataclass
class StepEvidence:
    """Evidence collected for a single step."""
    step_id: str
    step_description: str
    action: str
    target: Optional[Dict[str, Any]] = None
    input_value: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None
    output: Any = None
    screenshots: List[str] = field(default_factory=list)  # file paths
    dom_snapshots: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    duration_ms: int = 0


class EvidenceCollector:
    """
    Collects and manages evidence for a run.
    
    Handles screenshots, DOM snapshots, structured logs, and traces.
    """
    
    def __init__(self, run_id: str, evidence_dir: str = None):
        self.run_id = run_id
        self.evidence_dir = Path(evidence_dir or settings.evidence_dir) / run_id
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        
        self.records: List[EvidenceRecord] = []
        self.step_evidence: Dict[str, StepEvidence] = {}
        self.current_step: Optional[StepEvidence] = None
        
        # Set up structured logging
        self.logger = structlog.get_logger().bind(run_id=run_id)
    
    def start_step(self, step_id: str, description: str, action: str, target: Dict = None, input_value: str = None) -> StepEvidence:
        """Start collecting evidence for a step."""
        evidence = StepEvidence(
            step_id=step_id,
            step_description=description,
            action=action,
            target=target,
            input_value=input_value,
        )
        self.step_evidence[step_id] = evidence
        self.current_step = evidence
        
        self.logger.info(
            "step_started",
            step_id=step_id,
            description=description,
            action=action,
        )
        
        return evidence
    
    def end_step(self, success: bool, output: Any = None, error: str = None) -> None:
        """End evidence collection for current step."""
        if not self.current_step:
            return
        
        self.current_step.completed_at = datetime.utcnow()
        self.current_step.success = success
        self.current_step.output = output
        self.current_step.error = error
        self.current_step.duration_ms = int(
            (self.current_step.completed_at - self.current_step.started_at).total_seconds() * 1000
        )
        
        self.logger.info(
            "step_completed",
            step_id=self.current_step.step_id,
            success=success,
            duration_ms=self.current_step.duration_ms,
            error=error,
        )
        
        self.current_step = None
    
    async def capture_screenshot(self, surface, label: str = "") -> Optional[str]:
        """Capture and save a screenshot."""
        if not settings.capture_screenshots:
            return None
        
        try:
            screenshot = await surface.take_screenshot()
            filename = f"screenshot_{self.run_id}_{label}_{datetime.utcnow().strftime('%H%M%S')}.png"
            filepath = self.evidence_dir / filename
            filepath.write_bytes(screenshot)
            
            record = EvidenceRecord(
                run_id=self.run_id,
                step_id=self.current_step.step_id if self.current_step else None,
                type=EvidenceType.SCREENSHOT,
                data=screenshot,
                metadata={"label": label},
                file_path=str(filepath),
            )
            self.records.append(record)
            
            if self.current_step:
                self.current_step.screenshots.append(str(filepath))
            
            return str(filepath)
        except Exception as e:
            self.logger.error("screenshot_failed", error=str(e))
            return None
    
    async def capture_dom_snapshot(self, surface, label: str = "") -> Optional[str]:
        """Capture and save DOM snapshot."""
        if not settings.capture_dom_snapshots:
            return None
        
        try:
            # Get page content
            dom = await surface.page.content()
            filename = f"dom_{self.run_id}_{label}_{datetime.utcnow().strftime('%H%M%S')}.html"
            filepath = self.evidence_dir / filename
            filepath.write_text(dom)
            
            record = EvidenceRecord(
                run_id=self.run_id,
                step_id=self.current_step.step_id if self.current_step else None,
                type=EvidenceType.DOM_SNAPSHOT,
                data=dom,
                metadata={"label": label},
                file_path=str(filepath),
            )
            self.records.append(record)
            
            if self.current_step:
                self.current_step.dom_snapshots.append(str(filepath))
            
            return str(filepath)
        except Exception as e:
            self.logger.error("dom_snapshot_failed", error=str(e))
            return None
    
    async def capture_accessibility_tree(self, surface, label: str = "") -> Optional[str]:
        """Capture accessibility tree."""
        try:
            tree = await surface.get_accessibility_tree()
            filename = f"a11y_{self.run_id}_{label}_{datetime.utcnow().strftime('%H%M%S')}.json"
            filepath = self.evidence_dir / filename
            filepath.write_text(json.dumps(tree, indent=2))
            
            record = EvidenceRecord(
                run_id=self.run_id,
                step_id=self.current_step.step_id if self.current_step else None,
                type=EvidenceType.ACCESSIBILITY_TREE,
                data=tree,
                metadata={"label": label},
                file_path=str(filepath),
            )
            self.records.append(record)
            
            return str(filepath)
        except Exception as e:
            self.logger.error("accessibility_capture_failed", error=str(e))
            return None
    
    def add_log(self, message: str, level: str = "info", **kwargs) -> None:
        """Add a log entry to current step."""
        if self.current_step:
            self.current_step.logs.append(f"[{level.upper()}] {message}")
        
        # Also log via structlog
        getattr(self.logger, level)(message, **kwargs)
    
    def save_run_summary(self, run_result: Any) -> str:
        """Save complete run summary with all evidence."""
        summary = {
            "run_id": self.run_id,
            "timestamp": datetime.utcnow().isoformat(),
            "result": run_result.model_dump() if hasattr(run_result, 'model_dump') else str(run_result),
            "steps": [se.__dict__ for se in self.step_evidence.values()],
            "evidence_records": [r.to_dict() for r in self.records],
        }
        
        # Convert datetime objects
        for step in summary["steps"]:
            step["started_at"] = step["started_at"].isoformat() if isinstance(step["started_at"], datetime) else step["started_at"]
            step["completed_at"] = step["completed_at"].isoformat() if step.get("completed_at") else None
        
        filename = f"run_summary_{self.run_id}.json"
        filepath = self.evidence_dir / filename
        filepath.write_text(json.dumps(summary, indent=2, default=str))
        
        return str(filepath)
    
    def get_evidence_index(self) -> Dict[str, Any]:
        """Get index of all evidence for this run."""
        return {
            "run_id": self.run_id,
            "evidence_dir": str(self.evidence_dir),
            "total_records": len(self.records),
            "total_steps": len(self.step_evidence),
            "records_by_type": {
                t.value: len([r for r in self.records if r.type == t])
                for t in EvidenceType
            },
            "records": [r.to_dict() for r in self.records],
        }


class RunLogger:
    """Structured logger for automation runs."""
    
    def __init__(self, run_id: str, evidence_collector: EvidenceCollector = None):
        self.run_id = run_id
        self.evidence = evidence_collector
        self.logger = structlog.get_logger().bind(run_id=run_id)
    
    def log_agent_thought(self, thought: str, observation: str = None, action: str = None) -> None:
        """Log agent's reasoning (for discovery runs)."""
        self.logger.info(
            "agent_thought",
            thought=thought,
            observation=observation,
            planned_action=action,
        )
        if self.evidence and self.evidence.current_step:
            self.evidence.current_step.logs.append(f"[THOUGHT] {thought}")
            if action:
                self.evidence.current_step.logs.append(f"[PLANNED] {action}")
    
    def log_action(self, action: str, target: Dict = None, **kwargs) -> None:
        """Log an action being taken."""
        self.logger.info("action", action=action, target=target, **kwargs)
    
    def log_result(self, success: bool, output: Any = None, error: str = None, **kwargs) -> None:
        """Log action result."""
        self.logger.info("result", success=success, output=output, error=error, **kwargs)
    
    def log_error(self, error: str, context: Dict = None, **kwargs) -> None:
        """Log an error."""
        self.logger.error("error", error=error, context=context, **kwargs)
    
    def log_checkpoint(self, checkpoint: str, passed: bool, actual: str = None) -> None:
        """Log checkpoint verification."""
        self.logger.info("checkpoint", checkpoint=checkpoint, passed=passed, actual=actual)
    
    def log_safety_check(self, check: str, passed: bool, details: Dict = None) -> None:
        """Log safety guardrail check."""
        self.logger.info("safety_check", check=check, passed=passed, details=details)


def setup_structured_logging(log_level: str = "INFO", log_file: str = None) -> None:
    """Configure structlog for the application."""
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if log_file:
        # File output - JSON format
        processors.append(structlog.processors.JSONRenderer())
        import logging
        logging.basicConfig(
            level=getattr(logging, log_level),
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
    else:
        # Console output - pretty format
        processors.append(structlog.dev.ConsoleRenderer())
        import logging
        logging.basicConfig(
            level=getattr(logging, log_level),
            handlers=[logging.StreamHandler()],
        )
    
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )