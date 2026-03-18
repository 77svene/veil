"""
Self-Healing Automation Engine for veil
Automatically detects and corrects automation failures using adaptive strategies.
"""

import asyncio
import json
import logging
import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

from playwright.async_api import Page, ElementHandle, Locator, TimeoutError as PlaywrightTimeoutError

from veil.actor.element import Element
from veil.actor.page import Page as ActorPage
from veil.actor.utils import retry, timeout


logger = logging.getLogger(__name__)


class FailureType(Enum):
    """Types of automation failures that can be healed."""
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_NOT_INTERACTABLE = "element_not_interactable"
    ELEMENT_STALE = "element_stale"
    PAGE_CHANGED = "page_changed"
    TIMEOUT = "timeout"
    NAVIGATION_FAILED = "navigation_failed"
    CLICK_INTERCEPTED = "click_intercepted"
    UNKNOWN = "unknown"


class HealingStrategy(Enum):
    """Fallback strategies for self-healing."""
    ALTERNATIVE_SELECTORS = "alternative_selectors"
    WAIT_AND_RETRY = "wait_and_retry"
    SCROLL_INTO_VIEW = "scroll_into_view"
    JAVASCRIPT_FALLBACK = "javascript_fallback"
    VISUAL_LOCATOR = "visual_locator"
    ACCESSIBILITY_TREE = "accessibility_tree"
    PAGE_RELOAD = "page_reload"
    CONTEXT_RESET = "context_reset"


@dataclass
class HealingAttempt:
    """Record of a healing attempt."""
    timestamp: float
    failure_type: FailureType
    original_selector: str
    strategy: HealingStrategy
    success: bool
    new_selector: Optional[str] = None
    attempts: int = 1
    error_message: Optional[str] = None
    page_url: Optional[str] = None


@dataclass
class DomainPattern:
    """Pattern for matching domains/URLs."""
    domain: str
    path_pattern: Optional[str] = None
    exact_match: bool = False
    
    def matches(self, url: str) -> bool:
        """Check if URL matches this pattern."""
        parsed = urlparse(url)
        
        # Check domain
        if self.domain not in parsed.netloc:
            return False
            
        # Check path pattern if specified
        if self.path_pattern:
            if self.exact_match:
                return parsed.path == self.path_pattern
            else:
                return self.path_pattern in parsed.path
                
        return True


@dataclass
class SelectorStrategy:
    """A selector strategy with metadata."""
    selector: str
    selector_type: str  # 'css', 'xpath', 'text', 'role', 'testid'
    priority: int = 0
    success_rate: float = 1.0
    last_used: Optional[float] = None
    use_count: int = 0
    
    def __hash__(self):
        return hash((self.selector, self.selector_type))


class State(Enum):
    """State machine states for healing process."""
    IDLE = "idle"
    DETECTING_FAILURE = "detecting_failure"
    ANALYZING_FAILURE = "analyzing_failure"
    SELECTING_STRATEGY = "selecting_strategy"
    EXECUTING_STRATEGY = "executing_strategy"
    VALIDATING_RESULT = "validating_result"
    LEARNING = "learning"
    RECOVERED = "recovered"
    FAILED = "failed"


class HealingStateMachine:
    """State machine for managing the healing process."""
    
    def __init__(self):
        self.state = State.IDLE
        self.current_failure: Optional[FailureType] = None
        self.current_strategy: Optional[HealingStrategy] = None
        self.attempts: List[HealingAttempt] = []
        self.max_attempts = 3
        self.transition_callbacks: Dict[State, List[Callable]] = defaultdict(list)
        
    def transition_to(self, new_state: State, **context):
        """Transition to a new state."""
        old_state = self.state
        self.state = new_state
        
        logger.debug(f"State transition: {old_state.value} -> {new_state.value}")
        
        # Execute callbacks for this transition
        for callback in self.transition_callbacks.get(new_state, []):
            try:
                callback(old_state, new_state, **context)
            except Exception as e:
                logger.error(f"Error in state transition callback: {e}")
                
    def register_callback(self, state: State, callback: Callable):
        """Register a callback for state transitions."""
        self.transition_callbacks[state].append(callback)
        
    def can_retry(self) -> bool:
        """Check if we can retry healing."""
        return len(self.attempts) < self.max_attempts
        
    def record_attempt(self, attempt: HealingAttempt):
        """Record a healing attempt."""
        self.attempts.append(attempt)
        
    def reset(self):
        """Reset the state machine."""
        self.state = State.IDLE
        self.current_failure = None
        self.current_strategy = None
        self.attempts.clear()


class SelfHealingEngine:
    """
    Self-healing automation engine that detects and corrects automation failures.
    
    Features:
    - Automatic failure detection and classification
    - Multiple fallback strategies per failure type
    - Learning from successful recoveries
    - Domain-specific strategy caching
    - State machine for controlled healing process
    """
    
    def __init__(
        self,
        page: Page,
        cache_dir: Optional[Path] = None,
        enable_learning: bool = True,
        max_healing_attempts: int = 3,
        healing_timeout: float = 30.0
    ):
        self.page = page
        self.state_machine = HealingStateMachine()
        self.state_machine.max_attempts = max_healing_attempts
        
        # Strategy caches
        self.domain_strategies: Dict[str, Dict[str, List[SelectorStrategy]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.failure_patterns: Dict[str, List[HealingAttempt]] = defaultdict(list)
        
        # Configuration
        self.enable_learning = enable_learning
        self.healing_timeout = healing_timeout
        self.cache_dir = cache_dir or Path.home() / ".veil" / "healing_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Load cached strategies
        self._load_cached_strategies()
        
        # Register state machine callbacks
        self._register_state_callbacks()
        
        # Statistics
        self.stats = {
            "total_failures": 0,
            "successful_heals": 0,
            "failed_heals": 0,
            "strategy_usage": defaultdict(int)
        }
        
    def _register_state_callbacks(self):
        """Register callbacks for state machine transitions."""
        self.state_machine.register_callback(
            State.DETECTING_FAILURE,
            self._on_detecting_failure
        )
        self.state_machine.register_callback(
            State.SELECTING_STRATEGY,
            self._on_selecting_strategy
        )
        self.state_machine.register_callback(
            State.LEARNING,
            self._on_learning
        )
        
    def _on_detecting_failure(self, old_state: State, new_state: State, **context):
        """Callback when entering failure detection state."""
        self.stats["total_failures"] += 1
        
    def _on_selecting_strategy(self, old_state: State, new_state: State, **context):
        """Callback when selecting a healing strategy."""
        failure_type = context.get("failure_type")
        if failure_type:
            # Select best strategy based on past success
            strategy = self._select_best_strategy(failure_type, context.get("page_url"))
            self.state_machine.current_strategy = strategy
            
    def _on_learning(self, old_state: State, new_state: State, **context):
        """Callback when learning from a healing attempt."""
        if self.enable_learning:
            attempt = context.get("attempt")
            if attempt and attempt.success:
                self._update_strategy_success_rate(attempt)
                self._cache_successful_strategy(attempt)
                
    def _load_cached_strategies(self):
        """Load cached strategies from disk."""
        cache_file = self.cache_dir / "strategies.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    
                for domain, strategies in data.get("domain_strategies", {}).items():
                    for selector_key, strategy_list in strategies.items():
                        for strategy_data in strategy_list:
                            strategy = SelectorStrategy(
                                selector=strategy_data["selector"],
                                selector_type=strategy_data["selector_type"],
                                priority=strategy_data.get("priority", 0),
                                success_rate=strategy_data.get("success_rate", 1.0),
                                last_used=strategy_data.get("last_used"),
                                use_count=strategy_data.get("use_count", 0)
                            )
                            self.domain_strategies[domain][selector_key].append(strategy)
                            
                logger.info(f"Loaded cached strategies for {len(self.domain_strategies)} domains")
            except Exception as e:
                logger.error(f"Failed to load cached strategies: {e}")
                
    def _save_cached_strategies(self):
        """Save strategies to disk cache."""
        cache_file = self.cache_dir / "strategies.json"
        
        data = {"domain_strategies": {}}
        for domain, strategies in self.domain_strategies.items():
            data["domain_strategies"][domain] = {}
            for selector_key, strategy_list in strategies.items():
                data["domain_strategies"][domain][selector_key] = [
                    {
                        "selector": s.selector,
                        "selector_type": s.selector_type,
                        "priority": s.priority,
                        "success_rate": s.success_rate,
                        "last_used": s.last_used,
                        "use_count": s.use_count
                    }
                    for s in strategy_list
                ]
                
        try:
            with open(cache_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cached strategies: {e}")
            
    def _get_domain_key(self, url: Optional[str] = None) -> str:
        """Get domain key for strategy caching."""
        if url is None:
            try:
                url = self.page.url
            except:
                return "unknown"
                
        parsed = urlparse(url)
        return parsed.netloc or "unknown"
        
    def _get_selector_key(self, selector: str, selector_type: str = "css") -> str:
        """Generate a key for selector caching."""
        return f"{selector_type}:{hashlib.md5(selector.encode()).hexdigest()[:8]}"
        
    def _classify_failure(self, error: Exception, selector: str = "") -> FailureType:
        """Classify the type of failure based on the error."""
        error_str = str(error).lower()
        
        if "timeout" in error_str or isinstance(error, PlaywrightTimeoutError):
            return FailureType.TIMEOUT
        elif "not found" in error_str or "no element" in error_str:
            return FailureType.ELEMENT_NOT_FOUND
        elif "not visible" in error_str or "not interactable" in error_str:
            return FailureType.ELEMENT_NOT_INTERACTABLE
        elif "stale" in error_str:
            return FailureType.ELEMENT_STALE
        elif "navigation" in error_str or "page" in error_str:
            return FailureType.PAGE_CHANGED
        elif "click" in error_str and "intercepted" in error_str:
            return FailureType.CLICK_INTERCEPTED
        else:
            return FailureType.UNKNOWN
            
    def _select_best_strategy(
        self, 
        failure_type: FailureType, 
        page_url: Optional[str] = None
    ) -> HealingStrategy:
        """Select the best healing strategy based on failure type and history."""
        
        # Default strategy mapping
        strategy_mapping = {
            FailureType.ELEMENT_NOT_FOUND: [
                HealingStrategy.ALTERNATIVE_SELECTORS,
                HealingStrategy.WAIT_AND_RETRY,
                HealingStrategy.VISUAL_LOCATOR,
                HealingStrategy.ACCESSIBILITY_TREE
            ],
            FailureType.ELEMENT_NOT_INTERACTABLE: [
                HealingStrategy.SCROLL_INTO_VIEW,
                HealingStrategy.JAVASCRIPT_FALLBACK,
                HealingStrategy.WAIT_AND_RETRY
            ],
            FailureType.ELEMENT_STALE: [
                HealingStrategy.ALTERNATIVE_SELECTORS,
                HealingStrategy.PAGE_RELOAD
            ],
            FailureType.PAGE_CHANGED: [
                HealingStrategy.WAIT_AND_RETRY,
                HealingStrategy.PAGE_RELOAD,
                HealingStrategy.CONTEXT_RESET
            ],
            FailureType.TIMEOUT: [
                HealingStrategy.WAIT_AND_RETRY,
                HealingStrategy.ALTERNATIVE_SELECTORS
            ],
            FailureType.CLICK_INTERCEPTED: [
                HealingStrategy.JAVASCRIPT_FALLBACK,
                HealingStrategy.SCROLL_INTO_VIEW
            ]
        }
        
        strategies = strategy_mapping.get(failure_type, [HealingStrategy.WAIT_AND_RETRY])
        
        # TODO: Add logic to select based on past success rates
        return strategies[0] if strategies else HealingStrategy.WAIT_AND_RETRY
        
    def _update_strategy_success_rate(self, attempt: HealingAttempt):
        """Update success rate for a strategy based on attempt result."""
        if not attempt.new_selector:
            return
            
        domain_key = self._get_domain_key(attempt.page_url)
        selector_key = self._get_selector_key(attempt.new_selector)
        
        strategies = self.domain_strategies[domain_key].get(selector_key, [])
        for strategy in strategies:
            if strategy.selector == attempt.new_selector:
                # Update success rate using exponential moving average
                alpha = 0.3  # Learning rate
                new_rate = 1.0 if attempt.success else 0.0
                strategy.success_rate = (1 - alpha) * strategy.success_rate + alpha * new_rate
                strategy.use_count += 1
                strategy.last_used = attempt.timestamp
                break
                
    def _cache_successful_strategy(self, attempt: HealingAttempt):
        """Cache a successful strategy for future use."""
        if not attempt.new_selector or not attempt.success:
            return
            
        domain_key = self._get_domain_key(attempt.page_url)
        selector_key = self._get_selector_key(attempt.original_selector)
        
        # Create or update strategy
        strategy = SelectorStrategy(
            selector=attempt.new_selector,
            selector_type=self._infer_selector_type(attempt.new_selector),
            priority=10 if attempt.success else 0,
            success_rate=1.0 if attempt.success else 0.0,
            last_used=attempt.timestamp,
            use_count=1
        )
        
        # Add to cache
        strategies = self.domain_strategies[domain_key][selector_key]
        
        # Remove if already exists
        strategies = [s for s in strategies if s.selector != attempt.new_selector]
        strategies.append(strategy)
        
        # Sort by priority and success rate
        strategies.sort(key=lambda s: (s.priority, s.success_rate), reverse=True)
        self.domain_strategies[domain_key][selector_key] = strategies[:10]  # Keep top 10
        
        # Save to disk periodically
        if len(self.domain_strategies[domain_key]) % 5 == 0:
            self._save_cached_strategies()
            
    def _infer_selector_type(self, selector: str) -> str:
        """Infer selector type from selector string."""
        if selector.startswith("//") or selector.startswith("("):
            return "xpath"
        elif "text=" in selector or selector.startswith("'") or selector.startswith('"'):
            return "text"
        elif "[role=" in selector or selector.startswith("role="):
            return "role"
        elif "[data-testid" in selector or "testid=" in selector:
            return "testid"
        else:
            return "css"
            
    async def _generate_alternative_selectors(
        self, 
        original_selector: str,
        element_type: str = "element"
    ) -> List[str]:
        """Generate alternative selectors for an element."""
        alternatives = []
        
        try:
            # Try to find the element with original selector first
            element = await self.page.query_selector(original_selector)
            if not element:
                return alternatives
                
            # Get element attributes for generating alternatives
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            attributes = await element.evaluate("""el => {
                const attrs = {};
                for (const attr of el.attributes) {
                    attrs[attr.name] = attr.value;
                }
                return attrs;
            }""")
            
            # Generate CSS alternatives
            if "id" in attributes:
                alternatives.append(f"#{attributes['id']}")
                
            if "class" in attributes:
                classes = attributes["class"].split()
                if classes:
                    alternatives.append(f".{'.'.join(classes[:3])}")
                    
            if "name" in attributes:
                alternatives.append(f"[name='{attributes['name']}']")
                
            if "data-testid" in attributes:
                alternatives.append(f"[data-testid='{attributes['data-testid']}']")
                
            # Generate XPath alternatives
            xpath = await element.evaluate("""el => {
                function getXPath(el) {
                    if (el.id) return `//*[@id="${el.id}"]`;
                    if (el === document.body) return '/html/body';
                    
                    let ix = 0;
                    const siblings = el.parentNode ? el.parentNode.childNodes : [];
                    for (let i = 0; i < siblings.length; i++) {
                        const sibling = siblings[i];
                        if (sibling === el) {
                            return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                        }
                        if (sibling.nodeType === 1 && sibling.tagName === el.tagName) {
                            ix++;
                        }
                    }
                }
                return getXPath(el);
            }""")
            if xpath:
                alternatives.append(xpath)
                
            # Generate text-based alternatives for links and buttons
            if tag_name in ["a", "button"]:
                text = await element.evaluate("el => el.textContent?.trim()")
                if text and len(text) < 50:
                    alternatives.append(f"text={text}")
                    
        except Exception as e:
            logger.debug(f"Failed to generate alternative selectors: {e}")
            
        return alternatives
        
    async def _try_alternative_selectors(
        self,
        original_selector: str,
        action: Callable,
        max_alternatives: int = 3
    ) -> Tuple[bool, Optional[str]]:
        """Try alternative selectors for an action."""
        alternatives = await self._generate_alternative_selectors(original_selector)
        
        # Also check cached strategies
        domain_key = self._get_domain_key()
        selector_key = self._get_selector_key(original_selector)
        cached_strategies = self.domain_strategies.get(domain_key, {}).get(selector_key, [])
        
        # Combine and deduplicate
        all_selectors = [original_selector] + alternatives
        for strategy in cached_strategies:
            if strategy.selector not in all_selectors:
                all_selectors.append(strategy.selector)
                
        # Try each alternative
        for i, selector in enumerate(all_selectors[:max_alternatives + 1]):
            if i == 0:
                continue  # Skip original selector
                
            try:
                logger.debug(f"Trying alternative selector: {selector}")
                await action(selector)
                return True, selector
            except Exception as e:
                logger.debug(f"Alternative selector failed: {e}")
                continue
                
        return False, None
        
    async def _wait_and_retry(
        self,
        selector: str,
        action: Callable,
        wait_time: float = 2.0,
        max_retries: int = 3
    ) -> Tuple[bool, Optional[str]]:
        """Wait and retry an action."""
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(wait_time * (attempt + 1))
                await action(selector)
                return True, selector
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logger.debug(f"Retry {attempt + 1} failed: {e}")
                
        return False, None
        
    async def _scroll_into_view(self, selector: str) -> bool:
        """Scroll element into view."""
        try:
            element = await self.page.query_selector(selector)
            if element:
                await element.scroll_into_view_if_needed()
                return True
        except Exception as e:
            logger.debug(f"Scroll into view failed: {e}")
        return False
        
    async def _javascript_fallback(self, selector: str, action: str = "click") -> bool:
        """Use JavaScript to perform action as fallback."""
        try:
            if action == "click":
                await self.page.evaluate(f"""
                    const element = document.querySelector('{selector}');
                    if (element) {{
                        element.click();
                        return true;
                    }}
                    return false;
                """)
                return True
        except Exception as e:
            logger.debug(f"JavaScript fallback failed: {e}")
        return False
        
    async def _visual_locator(self, selector: str) -> Optional[str]:
        """Use visual locator as fallback (placeholder for ML-based approach)."""
        # This would integrate with a visual ML model in production
        # For now, return None to indicate not available
        return None
        
    async def _accessibility_tree_fallback(self, selector: str) -> Optional[str]:
        """Use accessibility tree as fallback."""
        try:
            # Get accessibility snapshot
            snapshot = await self.page.accessibility.snapshot()
            
            # Simple text-based matching (would be enhanced in production)
            element = await self.page.query_selector(selector)
            if element:
                text = await element.evaluate("el => el.textContent?.trim()")
                if text:
                    # Try to find by accessible name
                    # This is simplified - real implementation would traverse the tree
                    pass
        except Exception as e:
            logger.debug(f"Accessibility tree fallback failed: {e}")
        return None
        
    async def _page_reload(self) -> bool:
        """Reload the page as recovery strategy."""
        try:
            await self.page.reload(wait_until="networkidle")
            return True
        except Exception as e:
            logger.debug(f"Page reload failed: {e}")
            return False
            
    async def _context_reset(self) -> bool:
        """Reset browser context as recovery strategy."""
        try:
            # This would require integration with browser context management
            # For now, just clear cookies and storage
            await self.page.context.clear_cookies()
            await self.page.evaluate("window.localStorage.clear()")
            return True
        except Exception as e:
            logger.debug(f"Context reset failed: {e}")
            return False
            
    async def heal_action(
        self,
        action: Callable,
        selector: str,
        action_name: str = "action",
        element_type: str = "element",
        **kwargs
    ) -> Any:
        """
        Execute an action with self-healing capabilities.
        
        Args:
            action: Async function to execute (e.g., click, type)
            selector: CSS selector for the element
            action_name: Name of the action for logging
            element_type: Type of element for better healing
            **kwargs: Additional arguments for the action
            
        Returns:
            Result of the action
            
        Raises:
            Exception: If healing fails after all attempts
        """
        start_time = time.time()
        self.state_machine.reset()
        
        try:
            # Try the original action first
            self.state_machine.transition_to(State.EXECUTING_STRATEGY)
            result = await action(selector, **kwargs)
            self.state_machine.transition_to(State.RECOVERED)
            return result
            
        except Exception as original_error:
            logger.warning(f"{action_name} failed with selector '{selector}': {original_error}")
            
            # Start healing process
            self.state_machine.transition_to(
                State.DETECTING_FAILURE,
                error=original_error,
                selector=selector
            )
            
            failure_type = self._classify_failure(original_error, selector)
            self.state_machine.current_failure = failure_type
            
            # Record the failure attempt
            attempt = HealingAttempt(
                timestamp=time.time(),
                failure_type=failure_type,
                original_selector=selector,
                strategy=HealingStrategy.ALTERNATIVE_SELECTORS,  # Will be updated
                success=False,
                error_message=str(original_error),
                page_url=self.page.url
            )
            
            # Try healing strategies
            healed = False
            final_selector = selector
            
            while self.state_machine.can_retry() and not healed:
                self.state_machine.transition_to(
                    State.SELECTING_STRATEGY,
                    failure_type=failure_type,
                    page_url=self.page.url
                )
                
                strategy = self.state_machine.current_strategy
                attempt.strategy = strategy
                self.stats["strategy_usage"][strategy.value] += 1
                
                try:
                    self.state_machine.transition_to(State.EXECUTING_STRATEGY)
                    
                    if strategy == HealingStrategy.ALTERNATIVE_SELECTORS:
                        healed, new_selector = await self._try_alternative_selectors(
                            selector, action
                        )
                        if healed and new_selector:
                            final_selector = new_selector
                            attempt.new_selector = new_selector
                            
                    elif strategy == HealingStrategy.WAIT_AND_RETRY:
                        healed, _ = await self._wait_and_retry(selector, action)
                        
                    elif strategy == HealingStrategy.SCROLL_INTO_VIEW:
                        if await self._scroll_into_view(selector):
                            healed = await action(selector, **kwargs)
                            
                    elif strategy == HealingStrategy.JAVASCRIPT_FALLBACK:
                        healed = await self._javascript_fallback(selector, action_name)
                        
                    elif strategy == HealingStrategy.PAGE_RELOAD:
                        if await self._page_reload():
                            healed = await action(selector, **kwargs)
                            
                    elif strategy == HealingStrategy.CONTEXT_RESET:
                        if await self._context_reset():
                            healed = await action(selector, **kwargs)
                            
                    if healed:
                        self.state_machine.transition_to(State.VALIDATING_RESULT)
                        attempt.success = True
                        self.stats["successful_heals"] += 1
                        
                        # Learn from successful healing
                        self.state_machine.transition_to(
                            State.LEARNING,
                            attempt=attempt
                        )
                        
                        self.state_machine.transition_to(State.RECOVERED)
                        logger.info(f"Successfully healed {action_name} using {strategy.value}")
                        
                        # Execute the action with the healed selector
                        return await action(final_selector, **kwargs)
                        
                except Exception as healing_error:
                    logger.debug(f"Healing strategy {strategy.value} failed: {healing_error}")
                    attempt.error_message = str(healing_error)
                    
                # Record attempt
                self.state_machine.record_attempt(attempt)
                
            # All healing attempts failed
            self.state_machine.transition_to(State.FAILED)
            self.stats["failed_heals"] += 1
            
            # Record failed attempt for learning
            if self.enable_learning:
                self.failure_patterns[selector].append(attempt)
                
            raise Exception(
                f"Failed to heal {action_name} after {len(self.state_machine.attempts)} attempts. "
                f"Original error: {original_error}"
            )
            
    async def click_with_healing(self, selector: str, **kwargs) -> None:
        """Click an element with self-healing."""
        async def click_action(sel: str, **kw):
            element = await self.page.query_selector(sel)
            if not element:
                raise Exception(f"Element not found: {sel}")
            await element.click(**kw)
            
        await self.heal_action(click_action, selector, "click", **kwargs)
        
    async def type_with_healing(self, selector: str, text: str, **kwargs) -> None:
        """Type text into an element with self-healing."""
        async def type_action(sel: str, **kw):
            element = await self.page.query_selector(sel)
            if not element:
                raise Exception(f"Element not found: {sel}")
            await element.fill(text, **kw)
            
        await self.heal_action(type_action, selector, "type", **kwargs)
        
    async def wait_for_selector_with_healing(
        self, 
        selector: str, 
        timeout: float = 30000,
        **kwargs
    ) -> ElementHandle:
        """Wait for selector with self-healing."""
        async def wait_action(sel: str, **kw):
            return await self.page.wait_for_selector(sel, timeout=timeout, **kw)
            
        return await self.heal_action(wait_action, selector, "wait_for_selector", **kwargs)
        
    def get_stats(self) -> Dict[str, Any]:
        """Get healing statistics."""
        return {
            **self.stats,
            "cached_domains": len(self.domain_strategies),
            "total_cached_strategies": sum(
                len(strategies) 
                for domain_strategies in self.domain_strategies.values() 
                for strategies in domain_strategies.values()
            ),
            "state_machine_state": self.state_machine.state.value,
            "current_failure": self.state_machine.current_failure.value if self.state_machine.current_failure else None
        }
        
    def clear_cache(self):
        """Clear all cached strategies."""
        self.domain_strategies.clear()
        cache_file = self.cache_dir / "strategies.json"
        if cache_file.exists():
            cache_file.unlink()
        logger.info("Cleared healing strategy cache")


class ResilientElement(Element):
    """Element wrapper with self-healing capabilities."""
    
    def __init__(self, page: Page, selector: str, healing_engine: SelfHealingEngine):
        super().__init__(page, selector)
        self.healing_engine = healing_engine
        
    async def click(self, **kwargs) -> None:
        """Click with self-healing."""
        await self.healing_engine.click_with_healing(self.selector, **kwargs)
        
    async def type(self, text: str, **kwargs) -> None:
        """Type with self-healing."""
        await self.healing_engine.type_with_healing(self.selector, text, **kwargs)
        
    async def fill(self, text: str, **kwargs) -> None:
        """Fill with self-healing."""
        await self.healing_engine.type_with_healing(self.selector, text, **kwargs)


class ResilientPage(ActorPage):
    """Page wrapper with self-healing capabilities."""
    
    def __init__(self, page: Page, healing_engine: Optional[SelfHealingEngine] = None):
        super().__init__(page)
        self.healing_engine = healing_engine or SelfHealingEngine(page)
        
    async def query_selector(self, selector: str) -> Optional[ResilientElement]:
        """Query selector with self-healing."""
        try:
            element = await self.healing_engine.wait_for_selector_with_healing(
                selector, 
                timeout=5000
            )
            if element:
                return ResilientElement(self.page, selector, self.healing_engine)
        except Exception:
            pass
        return None
        
    async def click(self, selector: str, **kwargs) -> None:
        """Click with self-healing."""
        await self.healing_engine.click_with_healing(selector, **kwargs)
        
    async def type(self, selector: str, text: str, **kwargs) -> None:
        """Type with self-healing."""
        await self.healing_engine.type_with_healing(selector, text, **kwargs)
        
    async def goto(self, url: str, **kwargs) -> None:
        """Navigate with self-healing."""
        async def navigate_action(sel: str, **kw):
            # sel is actually the URL in this case
            return await super(ResilientPage, self).goto(sel, **kw)
            
        await self.healing_engine.heal_action(navigate_action, url, "goto", **kwargs)