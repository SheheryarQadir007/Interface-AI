"""
Web Surface Implementation using Playwright

This implements the BaseSurface interface for web applications.
Uses Playwright for robust, cross-browser automation.
"""

from typing import Any, Dict, List, Optional, Union
import asyncio
import json
import time
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Locator, Frame
from src.surface.base import (
    BaseSurface,
    SurfaceState,
    ElementInfo,
    SurfaceActionResult,
    LocatorResolver,
    ElementNotFoundError,
    SurfaceError,
    SurfaceTimeoutError,
)
from src.config import settings


class WebLocatorResolver(LocatorResolver):
    """Resolves locators for web surfaces using Playwright."""
    
    def __init__(self, surface: "WebSurface"):
        super().__init__(surface)
        self.page: Page = surface.page
    
    async def _try_strategy(
        self,
        strategy: str,
        value: str,
        locator: Dict[str, Any],
        timeout_ms: int
    ) -> Locator:
        frame = await self._get_frame(locator)
        
        if strategy == "test_id":
            return frame.locator(f"[data-testid='{value}']")
        elif strategy == "aria_label":
            return frame.locator(f"[aria-label='{value}']")
        elif strategy == "role_and_name":
            # value format: "role::name"
            if "::" in value:
                role, name = value.split("::", 1)
                return frame.get_by_role(role, name=name)
            return frame.get_by_role(value)
        elif strategy == "text_content":
            return frame.get_by_text(value, exact=False).first
        elif strategy == "css_selector":
            return frame.locator(value)
        elif strategy == "xpath":
            return frame.locator(f"xpath={value}")
        elif strategy == "coordinates":
            # value format: "x,y"
            x, y = map(int, value.split(","))
            return frame.locator(f"//*[@data-coords='{x},{y}']")
        else:
            raise ValueError(f"Unknown locator strategy: {strategy}")
    
    async def _get_frame(self, locator: Dict[str, Any]) -> Union[Page, Frame]:
        """Get the frame context if specified."""
        frame_context = locator.get("frame_context")
        if not frame_context:
            return self.page
        
        # Find frame by name or URL pattern
        for frame in self.page.frames:
            if frame_context in frame.name or frame_context in frame.url:
                return frame
        return self.page


class WebSurface(BaseSurface):
    """Playwright-based web surface implementation."""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._session_id = f"web_{int(time.time() * 1000)}"
        self._headless = config.get("headless", False) if config else False
        self._viewport = config.get("viewport", {"width": 1280, "height": 720}) if config else {"width": 1280, "height": 720}
        self._recording = False
        self._recording_path = None
    
    @property
    def page(self) -> Page:
        if not self._page:
            raise SurfaceError("Page not initialized. Call initialize() first.")
        return self._page
    
    @property
    def context(self) -> BrowserContext:
        if not self._context:
            raise SurfaceError("Context not initialized. Call initialize() first.")
        return self._context
    
    async def initialize(self) -> None:
        """Launch browser and create context/page."""
        self._playwright = await async_playwright().start()
        
        # Launch browser
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # Create context with realistic settings
        self._context = await self._browser.new_context(
            viewport=self._viewport,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )
        
        # Enable tracing for evidence
        await self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
        
        # Create page
        self._page = await self._context.new_page()
        
        # Set up event listeners for evidence
        self._page.on("console", lambda msg: None)  # Could log console messages
        self._page.on("pageerror", lambda err: None)  # Could log page errors
        
        # Navigate to blank page initially
        await self._page.goto("about:blank")
    
    async def close(self) -> None:
        """Close browser and clean up."""
        if self._context:
            await self._context.tracing.stop()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def navigate(self, url: str, wait_until: str = "networkidle") -> SurfaceActionResult:
        try:
            # Safety check
            if not self._is_allowed_domain(url):
                return SurfaceActionResult(
                    success=False,
                    error=f"Navigation to {url} not allowed by policy"
                )
            
            response = await self._page.goto(url, wait_until=wait_until, timeout=30000)
            await self._page.wait_for_load_state("domcontentloaded")
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output={"url": url, "status": response.status if response else None},
                new_state=state,
                evidence={"screenshot": await self.take_screenshot()}
            )
        except Exception as e:
            return SurfaceActionResult(
                success=False,
                error=f"Navigation failed: {str(e)}",
                evidence={"screenshot": await self.take_screenshot()}
            )
    
    def _is_allowed_domain(self, url: str) -> bool:
        """Check if URL domain is allowed."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return any(allowed in domain for allowed in settings.allowed_domains)
    
    async def click(self, locator: Dict[str, Any], **kwargs) -> SurfaceActionResult:
        try:
            resolver = WebLocatorResolver(self)
            element = await resolver.resolve(locator, kwargs.get("timeout_ms", 10000))
            
            # Wait for element to be actionable
            await element.wait_for(state="visible", timeout=kwargs.get("timeout_ms", 10000))
            await element.wait_for(state="enabled", timeout=kwargs.get("timeout_ms", 10000))
            
            # Click with options
            await element.click(
                force=kwargs.get("force", False),
                position=kwargs.get("position"),
                modifiers=kwargs.get("modifiers"),
                timeout=kwargs.get("timeout_ms", 10000)
            )
            
            # Wait for any navigation or network activity
            await self._page.wait_for_load_state("networkidle", timeout=5000)
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output={"clicked": True},
                new_state=state,
                evidence={"screenshot": await self.take_screenshot()}
            )
        except Exception as e:
            return SurfaceActionResult(
                success=False,
                error=f"Click failed: {str(e)}",
                evidence={"screenshot": await self.take_screenshot()}
            )
    
    async def type_text(self, locator: Dict[str, Any], text: str, **kwargs) -> SurfaceActionResult:
        try:
            resolver = WebLocatorResolver(self)
            element = await resolver.resolve(locator, kwargs.get("timeout_ms", 10000))
            
            await element.wait_for(state="visible", timeout=kwargs.get("timeout_ms", 10000))
            await element.wait_for(state="enabled", timeout=kwargs.get("timeout_ms", 10000))
            
            # Clear first if requested
            if kwargs.get("clear_first", True):
                await element.clear()
            
            # Type with delay for realism
            await element.type(text, delay=kwargs.get("delay", 50))
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output={"typed": len(text)},
                new_state=state,
                evidence={"screenshot": await self.take_screenshot()}
            )
        except Exception as e:
            return SurfaceActionResult(
                success=False,
                error=f"Type failed: {str(e)}",
                evidence={"screenshot": await self.take_screenshot()}
            )
    
    async def select_option(self, locator: Dict[str, Any], value: str, **kwargs) -> SurfaceActionResult:
        try:
            resolver = WebLocatorResolver(self)
            element = await resolver.resolve(locator, kwargs.get("timeout_ms", 10000))
            
            await element.wait_for(state="visible", timeout=kwargs.get("timeout_ms", 10000))
            await element.select_option(value=value)
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output={"selected": value},
                new_state=state,
                evidence={"screenshot": await self.take_screenshot()}
            )
        except Exception as e:
            return SurfaceActionResult(
                success=False,
                error=f"Select failed: {str(e)}",
                evidence={"screenshot": await self.take_screenshot()}
            )
    
    async def scroll(self, direction: str = "down", amount: int = 500, **kwargs) -> SurfaceActionResult:
        try:
            if direction == "down":
                await self._page.mouse.wheel(0, amount)
            elif direction == "up":
                await self._page.mouse.wheel(0, -amount)
            elif direction == "left":
                await self._page.mouse.wheel(-amount, 0)
            elif direction == "right":
                await self._page.mouse.wheel(amount, 0)
            
            await asyncio.sleep(0.3)  # Allow scroll to complete
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output={"scrolled": direction},
                new_state=state
            )
        except Exception as e:
            return SurfaceActionResult(success=False, error=f"Scroll failed: {str(e)}")
    
    async def wait_for(self, condition: str, timeout_ms: int = 10000, **kwargs) -> SurfaceActionResult:
        try:
            if condition.startswith("url:"):
                pattern = condition[4:]
                await self._page.wait_for_url(f"**/{pattern}**", timeout=timeout_ms)
            elif condition.startswith("element:"):
                locator_str = condition[8:]
                await self._page.wait_for_selector(locator_str, timeout=timeout_ms)
            elif condition == "networkidle":
                await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
            elif condition == "domcontentloaded":
                await self._page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            else:
                # Custom JavaScript condition
                await self._page.wait_for_function(condition, timeout=timeout_ms)
            
            state = await self.get_state()
            return SurfaceActionResult(success=True, new_state=state)
        except Exception as e:
            return SurfaceActionResult(success=False, error=f"Wait failed: {str(e)}")
    
    async def extract(self, locator: Dict[str, Any], attribute: str = "text", multiple: bool = False, **kwargs) -> SurfaceActionResult:
        try:
            resolver = WebLocatorResolver(self)
            element = await resolver.resolve(locator, kwargs.get("timeout_ms", 10000))
            
            await element.wait_for(state="attached", timeout=kwargs.get("timeout_ms", 10000))
            
            if multiple:
                elements = await element.all()
                results = []
                for el in elements:
                    if attribute == "text":
                        val = await el.inner_text()
                    elif attribute == "html":
                        val = await el.inner_html()
                    elif attribute == "value":
                        val = await el.input_value()
                    else:
                        val = await el.get_attribute(attribute)
                    results.append(val)
                output = results
            else:
                if attribute == "text":
                    output = await element.inner_text()
                elif attribute == "html":
                    output = await element.inner_html()
                elif attribute == "value":
                    output = await element.input_value()
                else:
                    output = await element.get_attribute(attribute)
            
            state = await self.get_state()
            return SurfaceActionResult(
                success=True,
                output=output,
                new_state=state
            )
        except Exception as e:
            return SurfaceActionResult(success=False, error=f"Extract failed: {str(e)}")
    
    async def hover(self, locator: Dict[str, Any], **kwargs) -> SurfaceActionResult:
        try:
            resolver = WebLocatorResolver(self)
            element = await resolver.resolve(locator, kwargs.get("timeout_ms", 10000))
            await element.hover()
            state = await self.get_state()
            return SurfaceActionResult(success=True, new_state=state)
        except Exception as e:
            return SurfaceActionResult(success=False, error=f"Hover failed: {str(e)}")
    
    async def press_key(self, key: str, **kwargs) -> SurfaceActionResult:
        try:
            await self._page.keyboard.press(key)
            state = await self.get_state()
            return SurfaceActionResult(success=True, new_state=state)
        except Exception as e:
            return SurfaceActionResult(success=False, error=f"Key press failed: {str(e)}")
    
    async def get_state(self) -> SurfaceState:
        """Get current page state for observation."""
        try:
            # Get accessibility tree for LLM
            accessibility_tree = await self._page.accessibility.snapshot()
            
            # Get visible elements with selector hints
            elements = await self._page.evaluate("""() => {
                const elements = [];
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_ELEMENT,
                    null,
                    false
                );
                let node;
                while (node = walker.nextNode()) {
                    const rect = node.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const style = window.getComputedStyle(node);
                        if (style.visibility !== 'hidden' && style.display !== 'none') {
                            elements.push({
                                tag: node.tagName.toLowerCase(),
                                id: node.id,
                                classes: Array.from(node.classList),
                                text: node.innerText?.substring(0, 200),
                                role: node.getAttribute('role'),
                                ariaLabel: node.getAttribute('aria-label'),
                                ariaLabelledBy: node.getAttribute('aria-labelledby'),
                                type: node.type,
                                name: node.name,
                                placeholder: node.placeholder,
                                value: node.value,
                                href: node.href,
                                boundingBox: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                                selectorHints: {
                                    testId: node.getAttribute('data-testid'),
                                    css: node.tagName.toLowerCase() + (node.id ? '#' + node.id : '') + (node.className ? '.' + node.className.split(' ').join('.') : ''),
                                }
                            });
                        }
                    }
                }
                return elements.slice(0, 200);  // Limit for token efficiency
            }""")
            
            self._current_state = SurfaceState(
                url=self._page.url,
                title=await self._page.title(),
                viewport=self._viewport,
                elements=[ElementInfo(**el) for el in elements],
                timestamp=time.time()
            )
            return self._current_state
        except Exception:
            return self._current_state or SurfaceState()
    
    async def take_screenshot(self) -> bytes:
        """Take a screenshot of the current page."""
        return await self._page.screenshot(full_page=True)
    
    async def get_accessibility_tree(self) -> Dict[str, Any]:
        """Get accessibility tree for LLM observation."""
        return await self._page.accessibility.snapshot()
    
    def find_element(self, locator: Dict[str, Any]) -> Locator:
        """Find element using locator (sync version for internal use)."""
        resolver = WebLocatorResolver(self)
        # This is async but we can't await here - use in async context
        return asyncio.create_task(resolver.resolve(locator))
    
    async def wait_for_element(self, locator: Dict[str, Any], timeout_ms: int = 10000) -> Locator:
        """Wait for element to be present and actionable."""
        resolver = WebLocatorResolver(self)
        element = await resolver.resolve(locator, timeout_ms)
        await element.wait_for(state="visible", timeout=timeout_ms)
        await element.wait_for(state="enabled", timeout=timeout_ms)
        return element
    
    async def pause_automation(self) -> Dict[str, Any]:
        """Pause automation for human handoff."""
        # Save tracing
        trace_path = f"/tmp/trace_{self._session_id}.zip"
        await self._context.tracing.stop(path=trace_path)
        
        return {
            "session_id": self._session_id,
            "url": self._page.url,
            "cookies": await self._context.cookies(),
            "local_storage": await self._page.evaluate("() => JSON.stringify(localStorage)"),
            "session_storage": await self._page.evaluate("() => JSON.stringify(sessionStorage)"),
            "trace_path": trace_path,
            "viewport": self._viewport,
        }
    
    async def resume_automation(self, context: Dict[str, Any]) -> None:
        """Resume automation from human handoff context."""
        # Restore cookies
        if "cookies" in context:
            await self._context.add_cookies(context["cookies"])
        
        # Restore local/session storage
        if "local_storage" in context:
            await self._page.evaluate(f"() => {{ Object.assign(localStorage, {context['local_storage']}); }}")
        if "session_storage" in context:
            await self._page.evaluate(f"() => {{ Object.assign(sessionStorage, {context['session_storage']}); }}")
        
        # Navigate back to the URL
        if "url" in context:
            await self._page.goto(context["url"], wait_until="domcontentloaded")
        
        # Restart tracing
        await self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
    
    def get_session_id(self) -> str:
        return self._session_id
    
    async def start_recording(self, path: str) -> None:
        """Start video recording."""
        self._recording = True
        self._recording_path = path
        await self._context.record_video(path=path)
    
    async def stop_recording(self) -> Optional[str]:
        """Stop video recording and return path."""
        self._recording = False
        # Video is saved automatically by Playwright
        return self._recording_path