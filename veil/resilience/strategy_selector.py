"""veil/resilience/strategy_selector.py"""
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import logging
from urllib.parse import urlparse

from veil.actor.page import Page
from veil.actor.element import Element

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    """Types of fallback strategies available."""
    CSS_SELECTOR = "css_selector"
    XPATH = "xpath"
    TEXT_CONTENT = "text_content"
    ARIA_LABEL = "aria_label"
    DATA_ATTRIBUTE = "data_attribute"
    VISUAL_HASH = "visual_hash"
    COORDINATE_BASED = "coordinate_based"
    JAVASCRIPT_INJECTION = "javascript_injection"
    FRAME_NAVIGATION = "frame_navigation"
    RETRY_WITH_DELAY = "retry_with_delay"


class FailureType(Enum):
    """Types of failures that can occur."""
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_NOT_INTERACTABLE = "element_not_interactable"
    ELEMENT_STALE = "element_stale"
    PAGE_CHANGED = "page_changed"
    TIMEOUT = "timeout"
    NAVIGATION_ERROR = "navigation_error"
    SCRIPT_ERROR = "script_error"
    UNKNOWN = "unknown"


@dataclass
class StrategyAttempt:
    """Record of a strategy attempt."""
    strategy_type: StrategyType
    selector: str
    timestamp: float
    success: bool
    duration_ms: float
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectorStrategy:
    """A specific selector strategy with its parameters."""
    strategy_type: StrategyType
    selector: str
    priority: float = 1.0  # Higher = more preferred
    success_rate: float = 0.0
    attempts: int = 0
    successes: int = 0
    last_used: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def update_success(self, success: bool):
        """Update success rate statistics."""
        self.attempts += 1
        if success:
            self.successes += 1
        self.success_rate = self.successes / self.attempts if self.attempts > 0 else 0.0
        self.last_used = time.time()


@dataclass
class AutomationState:
    """Current state of automation for a specific task."""
    task_id: str
    url_pattern: str
    element_description: str
    action: str
    current_strategy_index: int = 0
    attempts: List[StrategyAttempt] = field(default_factory=list)
    is_recovering: bool = False
    recovery_attempts: int = 0
    max_recovery_attempts: int = 3
    started_at: float = field(default_factory=time.time)
    last_success: Optional[float] = None
    
    @property
    def success_rate(self) -> float:
        """Calculate overall success rate for this task."""
        if not self.attempts:
            return 0.0
        successes = sum(1 for a in self.attempts if a.success)
        return successes / len(self.attempts)
    
    @property
    def recent_failures(self) -> List[StrategyAttempt]:
        """Get recent failures (last 5 attempts)."""
        return [a for a in self.attempts[-5:] if not a.success]


class StrategySelector:
    """
    Self-healing automation engine that detects when automation breaks
    and self-corrects using multiple fallback strategies.
    
    Tracks success/failure patterns and learns from failures to improve
    future reliability. Caches successful strategies per domain/URL pattern.
    """
    
    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize the strategy selector."""
        self.cache_dir = cache_dir or Path.home() / ".veil" / "strategy_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # State tracking
        self.active_states: Dict[str, AutomationState] = {}
        self.strategy_cache: Dict[str, Dict[str, SelectorStrategy]] = defaultdict(dict)
        self.domain_patterns: Dict[str, Set[str]] = defaultdict(set)
        
        # Learning parameters
        self.learning_rate = 0.1
        self.exploration_rate = 0.2  # Probability of trying new strategy
        self.min_attempts_for_confidence = 3
        
        # Strategy generators
        self.strategy_generators: Dict[StrategyType, Callable] = {
            StrategyType.CSS_SELECTOR: self._generate_css_strategies,
            StrategyType.XPATH: self._generate_xpath_strategies,
            StrategyType.TEXT_CONTENT: self._generate_text_strategies,
            StrategyType.ARIA_LABEL: self._generate_aria_strategies,
            StrategyType.DATA_ATTRIBUTE: self._generate_data_attribute_strategies,
            StrategyType.VISUAL_HASH: self._generate_visual_hash_strategies,
            StrategyType.COORDINATE_BASED: self._generate_coordinate_strategies,
            StrategyType.JAVASCRIPT_INJECTION: self._generate_js_strategies,
            StrategyType.FRAME_NAVIGATION: self._generate_frame_strategies,
            StrategyType.RETRY_WITH_DELAY: self._generate_retry_strategies,
        }
        
        # Load cached strategies
        self._load_cache()
    
    def _get_cache_key(self, url_pattern: str, element_description: str, action: str) -> str:
        """Generate a cache key for a specific task."""
        key_string = f"{url_pattern}:{element_description}:{action}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_domain_pattern(self, url: str) -> str:
        """Extract domain pattern from URL for caching."""
        parsed = urlparse(url)
        # Use domain + path pattern (without query params)
        path_parts = parsed.path.split('/')
        # Keep first 2 levels of path for pattern matching
        if len(path_parts) > 3:
            path_pattern = '/'.join(path_parts[:3]) + '/*'
        else:
            path_pattern = parsed.path
        return f"{parsed.netloc}{path_pattern}"
    
    def _load_cache(self):
        """Load cached strategies from disk."""
        cache_file = self.cache_dir / "strategy_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    for key, strategies_data in data.items():
                        self.strategy_cache[key] = {
                            strategy_type: SelectorStrategy(**strategy_data)
                            for strategy_type, strategy_data in strategies_data.items()
                        }
                logger.info(f"Loaded {len(self.strategy_cache)} cached strategy sets")
            except Exception as e:
                logger.warning(f"Failed to load strategy cache: {e}")
    
    def _save_cache(self):
        """Save cached strategies to disk."""
        cache_file = self.cache_dir / "strategy_cache.json"
        try:
            # Convert to serializable format
            data = {}
            for key, strategies in self.strategy_cache.items():
                data[key] = {
                    strategy_type.value: {
                        'strategy_type': strategy.strategy_type.value,
                        'selector': strategy.selector,
                        'priority': strategy.priority,
                        'success_rate': strategy.success_rate,
                        'attempts': strategy.attempts,
                        'successes': strategy.successes,
                        'last_used': strategy.last_used,
                        'metadata': strategy.metadata,
                    }
                    for strategy_type, strategy in strategies.items()
                }
            
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save strategy cache: {e}")
    
    def _generate_css_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate CSS selector strategies."""
        strategies = []
        
        # Try various CSS selector approaches
        selectors = []
        
        # By ID
        if element_info.get('id'):
            selectors.append(f"#{element_info['id']}")
        
        # By class combinations
        if element_info.get('classes'):
            classes = element_info['classes']
            if len(classes) > 0:
                selectors.append('.' + '.'.join(classes))
            # Try individual classes
            for cls in classes[:3]:  # Limit to first 3 classes
                selectors.append(f".{cls}")
        
        # By data attributes
        if element_info.get('data_attrs'):
            for attr, value in list(element_info['data_attrs'].items())[:2]:
                selectors.append(f"[{attr}='{value}']")
        
        # By tag + attributes
        tag = element_info.get('tag', 'div')
        if element_info.get('name'):
            selectors.append(f"{tag}[name='{element_info['name']}']")
        if element_info.get('type'):
            selectors.append(f"{tag}[type='{element_info['type']}']")
        
        # By role
        if element_info.get('role'):
            selectors.append(f"[role='{element_info['role']}']")
        
        # By aria attributes
        if element_info.get('aria_label'):
            selectors.append(f"[aria-label='{element_info['aria_label']}']")
        
        # Create strategy objects
        for i, selector in enumerate(selectors):
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.CSS_SELECTOR,
                selector=selector,
                priority=1.0 - (i * 0.1),  # Decreasing priority
                metadata={'selector_index': i}
            ))
        
        return strategies
    
    def _generate_xpath_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate XPath strategies."""
        strategies = []
        
        # Build XPath expressions
        xpaths = []
        
        # By text content
        if element_info.get('text'):
            text = element_info['text'][:50]  # Limit length
            xpaths.append(f"//*[contains(text(), '{text}')]")
        
        # By attribute combinations
        tag = element_info.get('tag', '*')
        if element_info.get('id'):
            xpaths.append(f"//{tag}[@id='{element_info['id']}']")
        
        if element_info.get('name'):
            xpaths.append(f"//{tag}[@name='{element_info['name']}']")
        
        # By position (as last resort)
        if element_info.get('position'):
            pos = element_info['position']
            xpaths.append(f"//{tag}[position()={pos}]")
        
        # Create strategy objects
        for i, xpath in enumerate(xpaths):
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.XPATH,
                selector=xpath,
                priority=0.9 - (i * 0.1),
                metadata={'xpath_index': i}
            ))
        
        return strategies
    
    def _generate_text_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate text-based strategies."""
        if not element_info.get('text'):
            return []
        
        strategies = []
        text = element_info['text']
        
        # Exact text match
        strategies.append(SelectorStrategy(
            strategy_type=StrategyType.TEXT_CONTENT,
            selector=f"text={text}",
            priority=0.8,
            metadata={'match_type': 'exact'}
        ))
        
        # Partial text match
        if len(text) > 10:
            partial = text[:20] + "..." if len(text) > 20 else text
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.TEXT_CONTENT,
                selector=f"text*={partial}",
                priority=0.7,
                metadata={'match_type': 'partial'}
            ))
        
        return strategies
    
    def _generate_aria_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate ARIA attribute strategies."""
        strategies = []
        
        aria_attrs = ['aria-label', 'aria-labelledby', 'aria-describedby', 'role']
        for attr in aria_attrs:
            if element_info.get(attr):
                strategies.append(SelectorStrategy(
                    strategy_type=StrategyType.ARIA_LABEL,
                    selector=f"[{attr}='{element_info[attr]}']",
                    priority=0.85,
                    metadata={'aria_attribute': attr}
                ))
        
        return strategies
    
    def _generate_data_attribute_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate data attribute strategies."""
        if not element_info.get('data_attrs'):
            return []
        
        strategies = []
        for attr, value in element_info['data_attrs'].items():
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.DATA_ATTRIBUTE,
                selector=f"[{attr}='{value}']",
                priority=0.75,
                metadata={'data_attribute': attr}
            ))
        
        return strategies
    
    def _generate_visual_hash_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate visual hash-based strategies."""
        # This would require screenshot analysis - placeholder for now
        return []
    
    def _generate_coordinate_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate coordinate-based strategies."""
        if not element_info.get('bounding_box'):
            return []
        
        strategies = []
        box = element_info['bounding_box']
        
        # Center coordinates
        center_x = box['x'] + box['width'] / 2
        center_y = box['y'] + box['height'] / 2
        
        strategies.append(SelectorStrategy(
            strategy_type=StrategyType.COORDINATE_BASED,
            selector=f"coordinates={center_x},{center_y}",
            priority=0.6,  # Lower priority as it's less reliable
            metadata={
                'bounding_box': box,
                'center': (center_x, center_y)
            }
        ))
        
        return strategies
    
    def _generate_js_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate JavaScript injection strategies."""
        strategies = []
        
        # JavaScript to find element by various means
        js_snippets = []
        
        if element_info.get('id'):
            js_snippets.append(f"document.getElementById('{element_info['id']}')")
        
        if element_info.get('name'):
            js_snippets.append(f"document.querySelector('[name=\"{element_info['name']}\"]')")
        
        # XPath via JavaScript
        if element_info.get('xpath'):
            js_snippets.append(
                f"document.evaluate('{element_info['xpath']}', document, null, "
                f"XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue"
            )
        
        for i, js in enumerate(js_snippets):
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.JAVASCRIPT_INJECTION,
                selector=js,
                priority=0.5 - (i * 0.1),
                metadata={'js_snippet_index': i}
            ))
        
        return strategies
    
    def _generate_frame_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate frame navigation strategies."""
        # Would need to detect iframes and try elements within them
        return []
    
    def _generate_retry_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate retry with delay strategies."""
        strategies = []
        
        # Simple retry with increasing delays
        delays = [0.5, 1.0, 2.0, 5.0]
        for i, delay in enumerate(delays):
            strategies.append(SelectorStrategy(
                strategy_type=StrategyType.RETRY_WITH_DELAY,
                selector=f"delay={delay}",
                priority=0.4 - (i * 0.05),
                metadata={'delay_seconds': delay, 'retry_index': i}
            ))
        
        return strategies
    
    async def generate_strategies(self, page: Page, element_info: Dict[str, Any]) -> List[SelectorStrategy]:
        """Generate all possible strategies for finding an element."""
        all_strategies = []
        
        for strategy_type, generator in self.strategy_generators.items():
            try:
                strategies = generator(page, element_info)
                all_strategies.extend(strategies)
            except Exception as e:
                logger.debug(f"Failed to generate {strategy_type} strategies: {e}")
        
        # Sort by priority (highest first)
        all_strategies.sort(key=lambda s: s.priority, reverse=True)
        
        return all_strategies
    
    def get_cached_strategies(self, url: str, element_description: str, action: str) -> List[SelectorStrategy]:
        """Get cached strategies for a specific task."""
        domain_pattern = self._get_domain_pattern(url)
        cache_key = self._get_cache_key(domain_pattern, element_description, action)
        
        if cache_key in self.strategy_cache:
            strategies = list(self.strategy_cache[cache_key].values())
            # Sort by success rate (highest first)
            strategies.sort(key=lambda s: s.success_rate, reverse=True)
            return strategies
        
        return []
    
    def update_strategy_success(self, url: str, element_description: str, action: str, 
                               strategy: SelectorStrategy, success: bool):
        """Update strategy success/failure statistics."""
        domain_pattern = self._get_domain_pattern(url)
        cache_key = self._get_cache_key(domain_pattern, element_description, action)
        
        if cache_key not in self.strategy_cache:
            self.strategy_cache[cache_key] = {}
        
        strategy_type_key = strategy.strategy_type
        if strategy_type_key not in self.strategy_cache[cache_key]:
            # Store new strategy
            self.strategy_cache[cache_key][strategy_type_key] = strategy
        else:
            # Update existing strategy
            existing = self.strategy_cache[cache_key][strategy_type_key]
            existing.update_success(success)
            
            # Update selector if this one succeeded and existing failed
            if success and existing.success_rate < 0.5:
                existing.selector = strategy.selector
        
        # Track domain patterns
        self.domain_patterns[domain_pattern].add(element_description)
        
        # Periodically save cache
        if len(self.strategy_cache) % 10 == 0:
            self._save_cache()
    
    def start_task(self, task_id: str, url: str, element_description: str, action: str) -> AutomationState:
        """Start tracking a new automation task."""
        domain_pattern = self._get_domain_pattern(url)
        
        state = AutomationState(
            task_id=task_id,
            url_pattern=domain_pattern,
            element_description=element_description,
            action=action
        )
        
        self.active_states[task_id] = state
        return state
    
    def record_attempt(self, task_id: str, strategy: SelectorStrategy, 
                      success: bool, duration_ms: float, error: Optional[str] = None):
        """Record a strategy attempt for a task."""
        if task_id not in self.active_states:
            logger.warning(f"Task {task_id} not found in active states")
            return
        
        state = self.active_states[task_id]
        
        attempt = StrategyAttempt(
            strategy_type=strategy.strategy_type,
            selector=strategy.selector,
            timestamp=time.time(),
            success=success,
            duration_ms=duration_ms,
            error=error,
            metadata=strategy.metadata
        )
        
        state.attempts.append(attempt)
        
        if success:
            state.last_success = time.time()
            state.is_recovering = False
            state.recovery_attempts = 0
            
            # Update cached strategy
            self.update_strategy_success(
                state.url_pattern,
                state.element_description,
                state.action,
                strategy,
                success=True
            )
        else:
            state.recovery_attempts += 1
            state.is_recovering = True
            
            # Update cached strategy
            self.update_strategy_success(
                state.url_pattern,
                state.element_description,
                state.action,
                strategy,
                success=False
            )
    
    def should_continue_recovery(self, task_id: str) -> bool:
        """Determine if we should continue trying to recover from failure."""
        if task_id not in self.active_states:
            return False
        
        state = self.active_states[task_id]
        
        # Check if we've exceeded max recovery attempts
        if state.recovery_attempts >= state.max_recovery_attempts:
            return False
        
        # Check if we have any strategies left to try
        cached_strategies = self.get_cached_strategies(
            state.url_pattern,
            state.element_description,
            state.action
        )
        
        tried_strategies = {a.strategy_type for a in state.attempts}
        available_strategies = [s for s in cached_strategies if s.strategy_type not in tried_strategies]
        
        return len(available_strategies) > 0
    
    def get_next_strategy(self, task_id: str, page: Page, 
                         element_info: Dict[str, Any]) -> Optional[SelectorStrategy]:
        """Get the next strategy to try for a task."""
        if task_id not in self.active_states:
            return None
        
        state = self.active_states[task_id]
        
        # First, try cached successful strategies
        cached_strategies = self.get_cached_strategies(
            state.url_pattern,
            state.element_description,
            state.action
        )
        
        tried_strategies = {a.strategy_type for a in state.attempts}
        
        # Filter to untried strategies with good success rates
        untried_cached = [
            s for s in cached_strategies 
            if s.strategy_type not in tried_strategies 
            and s.success_rate > 0.3
            and s.attempts >= self.min_attempts_for_confidence
        ]
        
        if untried_cached:
            # Use the best cached strategy
            return untried_cached[0]
        
        # If no good cached strategies, generate new ones
        # (This would be async in real implementation)
        # For now, return None to indicate we should generate strategies
        return None
    
    def get_failure_type(self, error_message: str) -> FailureType:
        """Determine the type of failure from an error message."""
        error_lower = error_message.lower()
        
        if "element not found" in error_lower or "no such element" in error_lower:
            return FailureType.ELEMENT_NOT_FOUND
        elif "not interactable" in error_lower or "element not visible" in error_lower:
            return FailureType.ELEMENT_NOT_INTERACTABLE
        elif "stale element" in error_lower:
            return FailureType.ELEMENT_STALE
        elif "timeout" in error_lower:
            return FailureType.TIMEOUT
        elif "navigation" in error_lower or "page changed" in error_lower:
            return FailureType.PAGE_CHANGED
        elif "script" in error_lower or "javascript" in error_lower:
            return FailureType.SCRIPT_ERROR
        else:
            return FailureType.UNKNOWN
    
    def analyze_failure_patterns(self, task_id: str) -> Dict[str, Any]:
        """Analyze failure patterns for a task to suggest improvements."""
        if task_id not in self.active_states:
            return {}
        
        state = self.active_states[task_id]
        analysis = {
            'total_attempts': len(state.attempts),
            'success_rate': state.success_rate,
            'recovery_attempts': state.recovery_attempts,
            'failure_types': defaultdict(int),
            'slowest_strategies': [],
            'most_common_errors': defaultdict(int),
        }
        
        for attempt in state.attempts:
            if not attempt.success:
                failure_type = self.get_failure_type(attempt.error or "")
                analysis['failure_types'][failure_type.value] += 1
                
                if attempt.error:
                    analysis['most_common_errors'][attempt.error] += 1
        
        # Find slowest strategies
        attempts_with_duration = [a for a in state.attempts if a.duration_ms > 0]
        if attempts_with_duration:
            attempts_with_duration.sort(key=lambda a: a.duration_ms, reverse=True)
            analysis['slowest_strategies'] = [
                {
                    'strategy': a.strategy_type.value,
                    'duration_ms': a.duration_ms,
                    'selector': a.selector
                }
                for a in attempts_with_duration[:3]
            ]
        
        return dict(analysis)
    
    def get_domain_insights(self, domain_pattern: str) -> Dict[str, Any]:
        """Get insights about strategies for a specific domain pattern."""
        insights = {
            'domain_pattern': domain_pattern,
            'total_elements_tracked': len(self.domain_patterns.get(domain_pattern, set())),
            'strategy_success_rates': {},
            'most_successful_strategies': [],
        }
        
        # Analyze strategies for this domain
        domain_strategies = []
        for cache_key, strategies in self.strategy_cache.items():
            if domain_pattern in cache_key:
                for strategy in strategies.values():
                    domain_strategies.append(strategy)
        
        if domain_strategies:
            # Calculate average success rates by strategy type
            strategy_type_stats = defaultdict(lambda: {'total_attempts': 0, 'total_successes': 0})
            for strategy in domain_strategies:
                stats = strategy_type_stats[strategy.strategy_type.value]
                stats['total_attempts'] += strategy.attempts
                stats['total_successes'] += strategy.successes
            
            for strategy_type, stats in strategy_type_stats.items():
                if stats['total_attempts'] > 0:
                    success_rate = stats['total_successes'] / stats['total_attempts']
                    insights['strategy_success_rates'][strategy_type] = {
                        'success_rate': success_rate,
                        'attempts': stats['total_attempts']
                    }
            
            # Find most successful strategies
            domain_strategies.sort(key=lambda s: s.success_rate, reverse=True)
            insights['most_successful_strategies'] = [
                {
                    'strategy_type': s.strategy_type.value,
                    'selector': s.selector,
                    'success_rate': s.success_rate,
                    'attempts': s.attempts
                }
                for s in domain_strategies[:5]
            ]
        
        return insights
    
    def cleanup_old_states(self, max_age_hours: int = 24):
        """Clean up old automation states."""
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        to_remove = []
        for task_id, state in self.active_states.items():
            if current_time - state.started_at > max_age_seconds:
                to_remove.append(task_id)
        
        for task_id in to_remove:
            del self.active_states[task_id]
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old automation states")
    
    def export_learning_data(self, output_path: Path):
        """Export learning data for analysis."""
        data = {
            'strategy_cache': {},
            'domain_patterns': dict(self.domain_patterns),
            'active_states': {},
        }
        
        # Convert strategy cache
        for cache_key, strategies in self.strategy_cache.items():
            data['strategy_cache'][cache_key] = {
                strategy_type.value: {
                    'success_rate': strategy.success_rate,
                    'attempts': strategy.attempts,
                    'successes': strategy.successes,
                    'selector': strategy.selector,
                }
                for strategy_type, strategy in strategies.items()
            }
        
        # Convert active states
        for task_id, state in self.active_states.items():
            data['active_states'][task_id] = {
                'url_pattern': state.url_pattern,
                'element_description': state.element_description,
                'action': state.action,
                'success_rate': state.success_rate,
                'total_attempts': len(state.attempts),
                'recovery_attempts': state.recovery_attempts,
            }
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Exported learning data to {output_path}")


class SelfHealingAutomation:
    """
    High-level interface for self-healing automation.
    Integrates with existing veil modules.
    """
    
    def __init__(self, strategy_selector: Optional[StrategySelector] = None):
        """Initialize self-healing automation."""
        self.strategy_selector = strategy_selector or StrategySelector()
        self.task_counter = 0
    
    async def find_element_with_healing(self, page: Page, 
                                       selector: str,
                                       element_description: str = "",
                                       action: str = "find",
                                       max_attempts: int = 5) -> Optional[Element]:
        """
        Find an element using self-healing strategies.
        
        Args:
            page: The page to search in
            selector: Initial selector to try
            element_description: Description of what we're looking for
            action: Action to perform (find, click, type, etc.)
            max_attempts: Maximum number of strategies to try
            
        Returns:
            Element if found, None otherwise
        """
        self.task_counter += 1
        task_id = f"task_{self.task_counter}_{int(time.time())}"
        
        # Start tracking this task
        state = self.strategy_selector.start_task(
            task_id=task_id,
            url=page.url,
            element_description=element_description or selector,
            action=action
        )
        
        # Try initial selector first
        element = await self._try_find_element(page, selector)
        if element:
            self.strategy_selector.record_attempt(
                task_id=task_id,
                strategy=SelectorStrategy(
                    strategy_type=StrategyType.CSS_SELECTOR,
                    selector=selector,
                    priority=1.0
                ),
                success=True,
                duration_ms=0
            )
            return element
        
        # Initial selector failed, try healing strategies
        logger.info(f"Initial selector failed, attempting self-healing for: {element_description}")
        
        # Generate element info for strategy generation
        element_info = await self._extract_element_info(page, selector, element_description)
        
        # Generate strategies
        strategies = await self.strategy_selector.generate_strategies(page, element_info)
        
        # Try each strategy
        for i, strategy in enumerate(strategies[:max_attempts]):
            if not self.strategy_selector.should_continue_recovery(task_id):
                break
            
            logger.debug(f"Trying strategy {i+1}/{len(strategies)}: {strategy.strategy_type.value}")
            
            start_time = time.time()
            try:
                if strategy.strategy_type == StrategyType.RETRY_WITH_DELAY:
                    # Handle delay strategy
                    delay = strategy.metadata.get('delay_seconds', 1.0)
                    await asyncio.sleep(delay)
                    element = await self._try_find_element(page, selector)
                elif strategy.strategy_type == StrategyType.COORDINATE_BASED:
                    # Handle coordinate-based strategy
                    element = await self._try_find_by_coordinates(page, strategy)
                elif strategy.strategy_type == StrategyType.JAVASCRIPT_INJECTION:
                    # Handle JavaScript strategy
                    element = await self._try_find_by_javascript(page, strategy)
                else:
                    # Handle selector-based strategies
                    element = await self._try_find_element(page, strategy.selector)
                
                duration_ms = (time.time() - start_time) * 1000
                
                if element:
                    self.strategy_selector.record_attempt(
                        task_id=task_id,
                        strategy=strategy,
                        success=True,
                        duration_ms=duration_ms
                    )
                    logger.info(f"Self-healing succeeded with {strategy.strategy_type.value}")
                    return element
                else:
                    self.strategy_selector.record_attempt(
                        task_id=task_id,
                        strategy=strategy,
                        success=False,
                        duration_ms=duration_ms,
                        error="Element not found"
                    )
            
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                self.strategy_selector.record_attempt(
                    task_id=task_id,
                    strategy=strategy,
                    success=False,
                    duration_ms=duration_ms,
                    error=str(e)
                )
                logger.debug(f"Strategy failed: {e}")
        
        # All strategies failed
        logger.warning(f"All self-healing strategies failed for: {element_description}")
        
        # Analyze failure for future improvement
        analysis = self.strategy_selector.analyze_failure_patterns(task_id)
        logger.debug(f"Failure analysis: {analysis}")
        
        return None
    
    async def _try_find_element(self, page: Page, selector: str) -> Optional[Element]:
        """Try to find an element with a given selector."""
        try:
            # This would use the actual Page.find_element method
            # For now, we'll simulate it
            element = page.find_element(selector)
            return element
        except Exception:
            return None
    
    async def _try_find_by_coordinates(self, page: Page, strategy: SelectorStrategy) -> Optional[Element]:
        """Try to find an element by coordinates."""
        # This would use coordinate-based clicking/finding
        # Implementation depends on the underlying browser automation library
        return None
    
    async def _try_find_by_javascript(self, page: Page, strategy: SelectorStrategy) -> Optional[Element]:
        """Try to find an element using JavaScript."""
        try:
            # Execute JavaScript to find element
            result = page.evaluate(strategy.selector)
            if result:
                # Convert result to Element if possible
                # This depends on the browser automation library
                return result
        except Exception:
            pass
        return None
    
    async def _extract_element_info(self, page: Page, selector: str, description: str) -> Dict[str, Any]:
        """Extract information about an element for strategy generation."""
        element_info = {
            'description': description,
            'original_selector': selector,
        }
        
        try:
            # Try to find element with original selector to get its properties
            element = page.find_element(selector)
            
            # Extract properties
            element_info.update({
                'tag': element.tag_name,
                'id': element.get_attribute('id'),
                'classes': element.get_attribute('class', '').split(),
                'name': element.get_attribute('name'),
                'type': element.get_attribute('type'),
                'text': element.text[:100] if element.text else None,
                'aria_label': element.get_attribute('aria-label'),
                'role': element.get_attribute('role'),
                'data_attrs': {
                    attr: element.get_attribute(attr)
                    for attr in element.get_attributes()
                    if attr.startswith('data-')
                },
                'bounding_box': element.bounding_box if hasattr(element, 'bounding_box') else None,
            })
            
            # Try to get XPath
            try:
                element_info['xpath'] = page.evaluate(
                    "function getXPath(element) { "
                    "  if (element.id !== '') return '//*[@id=\"' + element.id + '\"]'; "
                    "  if (element === document.body) return '/html/body'; "
                    "  var ix = 0; "
                    "  var siblings = element.parentNode.childNodes; "
                    "  for (var i = 0; i < siblings.length; i++) { "
                    "    var sibling = siblings[i]; "
                    "    if (sibling === element) "
                    "      return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']'; "
                    "    if (sibling.nodeType === 1 && sibling.tagName === element.tagName) ix++; "
                    "  } "
                    "}",
                    element
                )
            except Exception:
                pass
            
        except Exception as e:
            logger.debug(f"Could not extract element info: {e}")
        
        return element_info
    
    async def click_with_healing(self, page: Page, selector: str, description: str = "") -> bool:
        """Click an element with self-healing."""
        element = await self.find_element_with_healing(
            page=page,
            selector=selector,
            element_description=description or f"clickable element: {selector}",
            action="click"
        )
        
        if element:
            try:
                element.click()
                return True
            except Exception as e:
                logger.error(f"Failed to click element: {e}")
        
        return False
    
    async def type_with_healing(self, page: Page, selector: str, text: str, description: str = "") -> bool:
        """Type into an element with self-healing."""
        element = await self.find_element_with_healing(
            page=page,
            selector=selector,
            element_description=description or f"input element: {selector}",
            action="type"
        )
        
        if element:
            try:
                element.clear()
                element.type(text)
                return True
            except Exception as e:
                logger.error(f"Failed to type into element: {e}")
        
        return False


# Convenience functions for integration with existing codebase
def create_strategy_selector(cache_dir: Optional[Path] = None) -> StrategySelector:
    """Create a new strategy selector instance."""
    return StrategySelector(cache_dir)


def create_self_healing_automation(strategy_selector: Optional[StrategySelector] = None) -> SelfHealingAutomation:
    """Create a new self-healing automation instance."""
    return SelfHealingAutomation(strategy_selector)


# Example usage and integration points
async def example_usage():
    """Example of how to use the self-healing automation."""
    from veil.actor.page import Page
    
    # Create page instance (assuming this exists in the codebase)
    page = Page()
    
    # Create self-healing automation
    automation = create_self_healing_automation()
    
    # Try to click a button with self-healing
    success = await automation.click_with_healing(
        page=page,
        selector="#submit-button",
        description="Submit button"
    )
    
    if success:
        print("Successfully clicked button with self-healing")
    else:
        print("Failed to click button even with self-healing")
    
    # Try to type into an input field
    success = await automation.type_with_healing(
        page=page,
        selector="input[name='email']",
        text="user@example.com",
        description="Email input field"
    )
    
    # Get insights about domain patterns
    selector = automation.strategy_selector
    insights = selector.get_domain_insights("example.com/*")
    print(f"Domain insights: {insights}")


if __name__ == "__main__":
    # This would be imported and used by other modules
    pass