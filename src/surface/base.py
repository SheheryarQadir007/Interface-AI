"""
Surface Abstraction Layer

This module defines the interface between the automation system and the target surface
(web browser, desktop app, etc.). This abstraction allows the same artifact schema
and replay engine to work across different surface types.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum
import asyncio


class SurfaceType(str, Enum):
    WEB = "web"
    DESKTOP = "desktop"
    MOBILE = "mobile"


@dataclass
class ElementInfo:
    """Information about a UI element."""
    tag_name: str
    role: Optional[str] = None
    name: Optional[str] = None  # Accessible name
    text: Optional[str] = None
    attributes: Dict[str, str] = None
    bounding_box: Optional[Dict[str, float]] = None  # x, y, width, height
    is_visible: bool = True
    is_enabled: bool = True
    selector_hints: Dict[str, str] = None  # Suggested selectors
    
    def __post_init__(self):
        if self.attributes is None:
            self.attributes = {}
        if self.selector_hints is None:
            self.selector_hints = {}


@dataclass
class SurfaceState:
    """Current state of the surface."""
    url: Optional[str] = None
    title: Optional[str] = None
    viewport: Dict[str, int] = None
    elements: List[ElementInfo] = None
    screenshot: Optional[bytes] = None
    timestamp: float = 0
    
    def __post_init__(self):
        if self.viewport is None:
            self.viewport = {"width": 1280, "height": 720}
        if self.elements is None:
            self.elements = []


class SurfaceActionResult:
    """Result of an action on the surface."""
    def __init__(
        self,
        success: bool,
        output: Any = None,
        error: Optional[str] = None,
        new_state: Optional[SurfaceState] = None,
        evidence: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.output = output
        self.error = error
        self.new_state = new_state
        self.evidence = evidence or {}


class BaseSurface(ABC):
    """
    Abstract base class for surface automation.
    
    Implementations:
    - WebSurface: Playwright-based browser automation
    - DesktopSurface: OS-level automation (Windows UI Automation, macOS Accessibility, etc.)
    - MobileSurface: Appium or similar
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._session = None
        self._current_state: Optional[SurfaceState] = None
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the surface (launch browser, connect to app, etc.)."""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """Close the surface and clean up."""
        pass
    
    @abstractmethod
    async def navigate(self, url: str, wait_until: str = "networkidle") -> SurfaceActionResult:
        """Navigate to a URL."""
        pass
    
    @abstractmethod
    async def click(self, locator: Dict[str, Any], **kwargs) -> SurfaceActionResult:
        """Click an element."""
        pass
    
    @abstractmethod
    async def type_text(self, locator: Dict[str, Any], text: str, **kwargs) -> SurfaceActionResult:
        """Type text into an element."""
        pass
    
    @abstractmethod
    async def select_option(self, locator: Dict[str, Any], value: str, **kwargs) -> SurfaceActionResult:
        """Select an option from a dropdown."""
        pass
    
    @abstractmethod
    async def scroll(self, direction: str = "down", amount: int = 500, **kwargs) -> SurfaceActionResult:
        """Scroll the page."""
        pass
    
    @abstractmethod
    async def wait_for(self, condition: str, timeout_ms: int = 10000, **kwargs) -> SurfaceActionResult:
        """Wait for a condition."""
        pass
    
    @abstractmethod
    async def extract(self, locator: Dict[str, Any], attribute: str = "text", multiple: bool = False, **kwargs) -> SurfaceActionResult:
        """Extract data from element(s)."""
        pass
    
    @abstractmethod
    async def hover(self, locator: Dict[str, Any], **kwargs) -> SurfaceActionResult:
        """Hover over an element."""
        pass
    
    @abstractmethod
    async def press_key(self, key: str, **kwargs) -> SurfaceActionResult:
        """Press a keyboard key."""
        pass
    
    @abstractmethod
    async def get_state(self) -> SurfaceState:
        """Get current surface state (for observation)."""
        pass
    
    @abstractmethod
    async def take_screenshot(self) -> bytes:
        """Take a screenshot."""
        pass
    
    @abstractmethod
    async def get_accessibility_tree(self) -> Dict[str, Any]:
        """Get accessibility tree for LLM observation."""
        pass
    
    @abstractmethod
    def find_element(self, locator: Dict[str, Any]) -> Any:
        """Find element using locator strategy (returns surface-specific handle)."""
        pass
    
    @abstractmethod
    async def wait_for_element(self, locator: Dict[str, Any], timeout_ms: int = 10000) -> Any:
        """Wait for element to be present and actionable."""
        pass
    
    # Context management for human handoff
    @abstractmethod
    async def pause_automation(self) -> Dict[str, Any]:
        """Pause automation and return session context for human takeover."""
        pass
    
    @abstractmethod
    async def resume_automation(self, context: Dict[str, Any]) -> None:
        """Resume automation from human handoff context."""
        pass
    
    @abstractmethod
    def get_session_id(self) -> str:
        """Get unique session identifier."""
        pass


class LocatorResolver:
    """
    Resolves element locators to surface-specific element handles.
    
    This is the key abstraction that allows artifacts to be surface-agnostic.
    The same ElementLocator can be resolved differently for web vs desktop.
    """
    
    def __init__(self, surface: BaseSurface):
        self.surface = surface
    
    async def resolve(self, locator: Dict[str, Any], timeout_ms: int = 10000) -> Any:
        """
        Resolve a locator to an element handle using fallback chain.
        
        Args:
            locator: ElementLocator dict with primary and fallbacks
            timeout_ms: Maximum time to wait
            
        Returns:
            Surface-specific element handle
            
        Raises:
            ElementNotFoundError: If no strategy succeeds
        """
        strategies = []
        
        # Primary strategy
        if "primary" in locator:
            strategies.append((locator["primary"], locator["primary_value"]))
        
        # Fallback strategies
        for fb in locator.get("fallbacks", []):
            strategies.append((fb["strategy"], fb["value"]))
        
        last_error = None
        for strategy, value in strategies:
            try:
                element = await self._try_strategy(strategy, value, locator, timeout_ms)
                if element:
                    return element
            except Exception as e:
                last_error = e
                continue
        
        raise ElementNotFoundError(f"All locator strategies failed. Last error: {last_error}")
    
    async def _try_strategy(
        self,
        strategy: str,
        value: str,
        locator: Dict[str, Any],
        timeout_ms: int
    ) -> Any:
        """Try a single locator strategy."""
        # This is implemented per-surface-type
        # WebSurface uses Playwright locators
        # DesktopSurface uses UI Automation patterns
        raise NotImplementedError("Subclasses must implement _try_strategy")


class ElementNotFoundError(Exception):
    """Raised when element cannot be located."""
    pass


class SurfaceError(Exception):
    """Base exception for surface errors."""
    pass


class SurfaceTimeoutError(SurfaceError):
    """Raised when surface operation times out."""
    pass


class SurfacePermissionError(SurfaceError):
    """Raised when action is not permitted."""
    pass