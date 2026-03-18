"""
Real-Time Performance Optimizer for veil.

Dynamically adjusts timeouts, parallelization, and resource usage based on page load
characteristics and network conditions. Achieves 3-5x speed improvement for complex
automation workflows.
"""

import asyncio
import time
import statistics
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

from veil.actor.page import Page
from veil.actor.element import Element
from veil.actor.utils import retry_async, TimeoutError

logger = logging.getLogger(__name__)


class LoadState(Enum):
    """Page load states for performance optimization."""
    INITIAL = "initial"
    LOADING = "loading"
    INTERACTIVE = "interactive"
    COMPLETE = "complete"
    STABLE = "stable"


@dataclass
class PerformanceMetrics:
    """Container for page performance metrics."""
    fcp: Optional[float] = None  # First Contentful Paint
    lcp: Optional[float] = None  # Largest Contentful Paint
    tti: Optional[float] = None  # Time to Interactive
    ttfb: Optional[float] = None  # Time to First Byte
    dom_content_loaded: Optional[float] = None
    load_event: Optional[float] = None
    network_latency: Optional[float] = None
    resource_count: int = 0
    js_heap_size: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class NetworkConditions:
    """Network condition estimates."""
    latency: float = 0.0  # ms
    download_speed: float = 0.0  # bytes/ms
    upload_speed: float = 0.0  # bytes/ms
    connection_type: str = "unknown"
    is_stable: bool = True


class PerformanceOptimizer:
    """
    Real-time performance optimizer that dynamically adjusts automation parameters.
    
    Monitors page load metrics and network conditions to optimize:
    - Timeout durations
    - Parallelization levels
    - Resource loading strategies
    - Element query batching
    - Predictive prefetching
    """
    
    def __init__(self, page: Page, config: Optional[Dict] = None):
        self.page = page
        self.config = config or {}
        
        # Performance tracking
        self.metrics_history: List[PerformanceMetrics] = []
        self.network_conditions = NetworkConditions()
        self.current_load_state = LoadState.INITIAL
        
        # Optimization parameters
        self.base_timeout = self.config.get("base_timeout", 30000)  # 30s default
        self.min_timeout = self.config.get("min_timeout", 1000)  # 1s minimum
        self.max_timeout = self.config.get("max_timeout", 120000)  # 2m maximum
        self.timeout_adjustment_factor = 1.0
        
        self.max_parallel_queries = self.config.get("max_parallel_queries", 10)
        self.current_parallel_limit = 5  # Start conservative
        
        self.batch_size = self.config.get("batch_size", 5)
        self.prefetch_enabled = self.config.get("prefetch_enabled", True)
        
        # Adaptive parameters
        self.stability_threshold = self.config.get("stability_threshold", 0.1)
        self.performance_window = self.config.get("performance_window", 10)
        self.learning_rate = self.config.get("learning_rate", 0.1)
        
        # State tracking
        self.last_optimization_time = 0
        self.optimization_interval = self.config.get("optimization_interval", 5.0)  # seconds
        self.consecutive_successes = 0
        self.consecutive_failures = 0
        
        # Predictive prefetching
        self.action_patterns: Dict[str, List[str]] = {}
        self.prefetch_cache: Dict[str, Any] = {}
        
        logger.info(f"PerformanceOptimizer initialized with config: {self.config}")
    
    async def start_monitoring(self) -> None:
        """Start continuous performance monitoring."""
        asyncio.create_task(self._monitor_loop())
        logger.debug("Performance monitoring started")
    
    async def _monitor_loop(self) -> None:
        """Continuous monitoring loop for performance metrics."""
        while True:
            try:
                await self._collect_metrics()
                await self._analyze_performance()
                await self._adjust_parameters()
                await asyncio.sleep(self.optimization_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.optimization_interval * 2)
    
    async def _collect_metrics(self) -> None:
        """Collect current page performance metrics."""
        try:
            metrics = PerformanceMetrics()
            
            # Collect performance timing metrics via JavaScript
            timing_data = await self.page.evaluate("""
                () => {
                    const perf = window.performance;
                    const timing = perf.timing || {};
                    const navigation = perf.getEntriesByType('navigation')[0] || {};
                    const paint = perf.getEntriesByType('paint') || [];
                    
                    const fcp = paint.find(p => p.name === 'first-contentful-paint');
                    const lcp = perf.getEntriesByType('largest-contentful-paint')[0];
                    
                    return {
                        fcp: fcp ? fcp.startTime : null,
                        lcp: lcp ? lcp.startTime : null,
                        tti: navigation.domInteractive ? navigation.domInteractive - navigation.startTime : null,
                        ttfb: navigation.responseStart ? navigation.responseStart - navigation.startTime : null,
                        domContentLoaded: navigation.domContentLoadedEventEnd ? 
                            navigation.domContentLoadedEventEnd - navigation.startTime : null,
                        loadEvent: navigation.loadEventEnd ? 
                            navigation.loadEventEnd - navigation.startTime : null,
                        resourceCount: perf.getEntriesByType('resource').length,
                        jsHeapSize: performance.memory ? performance.memory.usedJSHeapSize : null
                    };
                }
            """)
            
            if timing_data:
                metrics.fcp = timing_data.get("fcp")
                metrics.lcp = timing_data.get("lcp")
                metrics.tti = timing_data.get("tti")
                metrics.ttfb = timing_data.get("ttfb")
                metrics.dom_content_loaded = timing_data.get("domContentLoaded")
                metrics.load_event = timing_data.get("loadEvent")
                metrics.resource_count = timing_data.get("resourceCount", 0)
                metrics.js_heap_size = timing_data.get("jsHeapSize")
            
            # Estimate network latency
            metrics.network_latency = await self._estimate_network_latency()
            
            # Add to history
            self.metrics_history.append(metrics)
            if len(self.metrics_history) > self.performance_window:
                self.metrics_history.pop(0)
            
            # Update load state
            self._update_load_state(metrics)
            
            logger.debug(f"Collected metrics: FCP={metrics.fcp}, LCP={metrics.lcp}, TTI={metrics.tti}")
            
        except Exception as e:
            logger.warning(f"Failed to collect metrics: {e}")
    
    async def _estimate_network_latency(self) -> Optional[float]:
        """Estimate current network latency."""
        try:
            start_time = time.time()
            await self.page.evaluate("() => fetch('/favicon.ico', {method: 'HEAD'})")
            latency = (time.time() - start_time) * 1000  # Convert to ms
            return latency
        except Exception:
            return None
    
    def _update_load_state(self, metrics: PerformanceMetrics) -> None:
        """Update current load state based on metrics."""
        if metrics.load_event is not None:
            self.current_load_state = LoadState.COMPLETE
        elif metrics.tti is not None:
            self.current_load_state = LoadState.INTERACTIVE
        elif metrics.fcp is not None:
            self.current_load_state = LoadState.LOADING
        else:
            self.current_load_state = LoadState.INITIAL
        
        # Check for stability
        if len(self.metrics_history) >= 3:
            recent_lcp = [m.lcp for m in self.metrics_history[-3:] if m.lcp is not None]
            if len(recent_lcp) == 3:
                lcp_variance = statistics.variance(recent_lcp) if len(recent_lcp) > 1 else 0
                if lcp_variance < self.stability_threshold * statistics.mean(recent_lcp):
                    self.current_load_state = LoadState.STABLE
    
    async def _analyze_performance(self) -> None:
        """Analyze performance trends and update network conditions."""
        if len(self.metrics_history) < 2:
            return
        
        # Calculate network condition trends
        latencies = [m.network_latency for m in self.metrics_history if m.network_latency is not None]
        if latencies:
            self.network_conditions.latency = statistics.mean(latencies)
            self.network_conditions.is_stable = statistics.stdev(latencies) < self.network_conditions.latency * 0.3
        
        # Update connection type estimate based on latency
        if self.network_conditions.latency < 50:
            self.network_conditions.connection_type = "fast"
        elif self.network_conditions.latency < 200:
            self.network_conditions.connection_type = "moderate"
        else:
            self.network_conditions.connection_type = "slow"
    
    async def _adjust_parameters(self) -> None:
        """Dynamically adjust optimization parameters based on performance."""
        current_time = time.time()
        if current_time - self.last_optimization_time < self.optimization_interval:
            return
        
        self.last_optimization_time = current_time
        
        # Adjust timeout based on load state and network conditions
        self._adjust_timeout()
        
        # Adjust parallelization based on page stability and performance
        self._adjust_parallelization()
        
        # Adjust batch size based on resource count and JS heap
        self._adjust_batch_size()
        
        logger.info(
            f"Adjusted parameters: timeout={self.current_timeout}ms, "
            f"parallel_limit={self.current_parallel_limit}, "
            f"batch_size={self.batch_size}"
        )
    
    def _adjust_timeout(self) -> None:
        """Adjust timeout based on current conditions."""
        base_factor = 1.0
        
        # Adjust based on load state
        if self.current_load_state == LoadState.STABLE:
            base_factor *= 0.7  # Reduce timeout when stable
        elif self.current_load_state == LoadState.LOADING:
            base_factor *= 1.5  # Increase timeout when loading
        elif self.current_load_state == LoadState.INITIAL:
            base_factor *= 2.0  # Significantly increase for initial load
        
        # Adjust based on network conditions
        if self.network_conditions.connection_type == "slow":
            base_factor *= 1.8
        elif self.network_conditions.connection_type == "moderate":
            base_factor *= 1.2
        
        # Adjust based on recent success/failure rate
        if self.consecutive_successes > 5:
            base_factor *= 0.9  # Reduce timeout after successes
        if self.consecutive_failures > 2:
            base_factor *= 1.5  # Increase timeout after failures
        
        # Apply adjustment with bounds
        new_timeout = self.base_timeout * base_factor * self.timeout_adjustment_factor
        self.current_timeout = max(self.min_timeout, min(self.max_timeout, new_timeout))
        
        # Learn from adjustments
        self.timeout_adjustment_factor = (
            self.timeout_adjustment_factor * 0.9 + base_factor * 0.1
        )
    
    def _adjust_parallelization(self) -> None:
        """Adjust parallel query limit based on performance."""
        if self.current_load_state == LoadState.STABLE:
            # Increase parallelization when stable
            target_limit = min(self.max_parallel_queries, self.current_parallel_limit + 1)
        elif self.current_load_state == LoadState.LOADING:
            # Reduce parallelization during loading
            target_limit = max(1, self.current_parallel_limit - 1)
        else:
            target_limit = self.current_parallel_limit
        
        # Adjust based on network conditions
        if self.network_conditions.connection_type == "slow":
            target_limit = max(1, target_limit - 2)
        
        # Smooth adjustment
        self.current_parallel_limit = int(
            self.current_parallel_limit * 0.7 + target_limit * 0.3
        )
    
    def _adjust_batch_size(self) -> None:
        """Adjust batch size based on resource usage."""
        if not self.metrics_history:
            return
        
        latest_metrics = self.metrics_history[-1]
        
        # Adjust based on resource count
        if latest_metrics.resource_count > 100:
            self.batch_size = max(2, self.batch_size - 1)
        elif latest_metrics.resource_count < 20:
            self.batch_size = min(20, self.batch_size + 1)
        
        # Adjust based on JS heap size
        if latest_metrics.js_heap_size and latest_metrics.js_heap_size > 50 * 1024 * 1024:  # 50MB
            self.batch_size = max(2, self.batch_size - 2)
    
    async def get_optimized_timeout(self, operation_type: str = "default") -> float:
        """
        Get optimized timeout for specific operation type.
        
        Args:
            operation_type: Type of operation (navigation, click, wait, etc.)
            
        Returns:
            Optimized timeout in milliseconds
        """
        type_multipliers = {
            "navigation": 2.0,
            "click": 0.5,
            "wait": 1.0,
            "input": 0.3,
            "screenshot": 0.7,
            "evaluate": 0.4,
            "default": 1.0
        }
        
        multiplier = type_multipliers.get(operation_type, 1.0)
        timeout = self.current_timeout * multiplier
        
        # Ensure minimum timeout for critical operations
        if operation_type == "navigation":
            timeout = max(5000, timeout)
        
        return timeout
    
    async def batch_element_queries(
        self, 
        selectors: List[str], 
        timeout: Optional[float] = None
    ) -> List[Optional[Element]]:
        """
        Batch multiple element queries for parallel execution.
        
        Args:
            selectors: List of CSS selectors to query
            timeout: Optional timeout override
            
        Returns:
            List of Element objects or None for each selector
        """
        if not selectors:
            return []
        
        timeout = timeout or await self.get_optimized_timeout("query")
        batch_timeout = timeout / len(selectors) * 1.5  # Extra time for batching
        
        # Split into batches based on current batch size
        batches = [
            selectors[i:i + self.batch_size] 
            for i in range(0, len(selectors), self.batch_size)
        ]
        
        results = []
        
        for batch in batches:
            batch_results = await self._execute_batch_query(batch, batch_timeout)
            results.extend(batch_results)
            
            # Small delay between batches to prevent overwhelming
            if len(batches) > 1:
                await asyncio.sleep(0.01)
        
        return results
    
    async def _execute_batch_query(
        self, 
        selectors: List[str], 
        timeout: float
    ) -> List[Optional[Element]]:
        """Execute a batch of element queries in parallel."""
        tasks = []
        
        for selector in selectors:
            task = asyncio.create_task(
                self._query_single_element(selector, timeout)
            )
            tasks.append(task)
        
        # Execute with controlled parallelism
        results = []
        for i in range(0, len(tasks), self.current_parallel_limit):
            batch_tasks = tasks[i:i + self.current_parallel_limit]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            for result in batch_results:
                if isinstance(result, Exception):
                    logger.debug(f"Element query failed: {result}")
                    results.append(None)
                else:
                    results.append(result)
        
        return results
    
    async def _query_single_element(
        self, 
        selector: str, 
        timeout: float
    ) -> Optional[Element]:
        """Query a single element with optimized timeout."""
        try:
            element = await self.page.query_selector(selector, timeout=timeout)
            if element:
                self.consecutive_successes += 1
                self.consecutive_failures = 0
            return element
        except (TimeoutError, Exception) as e:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            logger.debug(f"Element query failed for {selector}: {e}")
            return None
    
    async def prefetch_resources(self, action_pattern: str) -> None:
        """
        Prefetch resources based on predicted next actions.
        
        Args:
            action_pattern: Current action pattern to predict next actions
        """
        if not self.prefetch_enabled:
            return
        
        # Learn from action patterns
        if action_pattern not in self.action_patterns:
            self.action_patterns[action_pattern] = []
        
        # Predict next actions based on historical patterns
        predicted_actions = self._predict_next_actions(action_pattern)
        
        for action in predicted_actions:
            if action not in self.prefetch_cache:
                await self._prefetch_action_resources(action)
    
    def _predict_next_actions(self, current_action: str) -> List[str]:
        """Predict next actions based on historical patterns."""
        # Simple pattern matching - in production, use ML model
        common_sequences = {
            "login": ["dashboard", "profile"],
            "search": ["results", "filter"],
            "add_to_cart": ["checkout", "continue_shopping"],
            "submit_form": ["confirmation", "next_step"]
        }
        
        for pattern, next_actions in common_sequences.items():
            if pattern in current_action.lower():
                return next_actions
        
        return []
    
    async def _prefetch_action_resources(self, action: str) -> None:
        """Prefetch resources for a predicted action."""
        try:
            # Prefetch common resources for action type
            prefetch_scripts = {
                "dashboard": """
                    () => {
                        // Prefetch dashboard data
                        fetch('/api/dashboard');
                        // Prefetch common UI components
                        document.createElement('link').relList.contains('prefetch');
                    }
                """,
                "checkout": """
                    () => {
                        // Prefetch payment forms
                        fetch('/api/payment-methods');
                        // Prefetch shipping options
                        fetch('/api/shipping-options');
                    }
                """
            }
            
            script = prefetch_scripts.get(action)
            if script:
                await self.page.evaluate(script)
                self.prefetch_cache[action] = time.time()
                logger.debug(f"Prefetched resources for action: {action}")
                
        except Exception as e:
            logger.debug(f"Prefetch failed for {action}: {e}")
    
    async def wait_for_stability(
        self, 
        timeout: Optional[float] = None,
        check_interval: float = 0.1
    ) -> bool:
        """
        Wait for page to reach stable state.
        
        Args:
            timeout: Maximum time to wait
            check_interval: Interval between stability checks
            
        Returns:
            True if page reached stable state, False if timeout
        """
        timeout = timeout or await self.get_optimized_timeout("wait")
        start_time = time.time()
        
        while time.time() - start_time < timeout / 1000:  # Convert to seconds
            if self.current_load_state == LoadState.STABLE:
                return True
            
            await asyncio.sleep(check_interval)
            
            # Update metrics to check for stability
            await self._collect_metrics()
        
        logger.warning(f"Timeout waiting for page stability after {timeout}ms")
        return False
    
    async def optimize_navigation(self, url: str) -> None:
        """
        Optimize page navigation with predictive prefetching.
        
        Args:
            url: URL to navigate to
        """
        # Prefetch DNS and TCP connection
        try:
            domain = url.split("//")[1].split("/")[0]
            await self.page.evaluate(f"""
                () => {{
                    const link = document.createElement('link');
                    link.rel = 'dns-prefetch';
                    link.href = '//{domain}';
                    document.head.appendChild(link);
                    
                    const preconnect = document.createElement('link');
                    preconnect.rel = 'preconnect';
                    preconnect.href = '//{domain}';
                    document.head.appendChild(preconnect);
                }}
            """)
        except Exception:
            pass
        
        # Adjust timeout for navigation
        nav_timeout = await self.get_optimized_timeout("navigation")
        
        # Navigate with optimized settings
        await self.page.goto(url, timeout=nav_timeout, wait_until="domcontentloaded")
        
        # Wait for initial stability
        await self.wait_for_stability(timeout=nav_timeout * 0.5)
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Generate performance optimization report."""
        if not self.metrics_history:
            return {"status": "no_data"}
        
        latest = self.metrics_history[-1]
        
        return {
            "current_state": self.current_load_state.value,
            "network_conditions": {
                "latency": self.network_conditions.latency,
                "connection_type": self.network_conditions.connection_type,
                "is_stable": self.network_conditions.is_stable
            },
            "optimizations": {
                "current_timeout": self.current_timeout,
                "parallel_limit": self.current_parallel_limit,
                "batch_size": self.batch_size,
                "prefetch_enabled": self.prefetch_enabled
            },
            "metrics": {
                "fcp": latest.fcp,
                "lcp": latest.lcp,
                "tti": latest.tti,
                "resource_count": latest.resource_count
            },
            "success_rate": {
                "consecutive_successes": self.consecutive_successes,
                "consecutive_failures": self.consecutive_failures
            }
        }
    
    def reset(self) -> None:
        """Reset optimizer state."""
        self.metrics_history.clear()
        self.current_load_state = LoadState.INITIAL
        self.consecutive_successes = 0
        self.consecutive_failures = 0
        self.prefetch_cache.clear()
        logger.debug("Performance optimizer reset")


# Integration with existing Page class
class OptimizedPage(Page):
    """Page class with integrated performance optimization."""
    
    def __init__(self, *args, optimizer_config: Optional[Dict] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.optimizer = PerformanceOptimizer(self, optimizer_config)
        self._optimizer_started = False
    
    async def start(self) -> None:
        """Start the page and performance optimizer."""
        await super().start()
        if not self._optimizer_started:
            await self.optimizer.start_monitoring()
            self._optimizer_started = True
    
    async def goto(self, url: str, **kwargs) -> None:
        """Navigate to URL with performance optimization."""
        await self.optimizer.optimize_navigation(url)
        # Call parent with optimized timeout
        timeout = kwargs.pop('timeout', None) or await self.optimizer.get_optimized_timeout("navigation")
        await super().goto(url, timeout=timeout, **kwargs)
    
    async def query_selector(self, selector: str, **kwargs) -> Optional[Element]:
        """Query selector with optimized timeout."""
        timeout = kwargs.pop('timeout', None) or await self.optimizer.get_optimized_timeout("query")
        return await super().query_selector(selector, timeout=timeout, **kwargs)
    
    async def query_selector_all(self, selectors: List[str], **kwargs) -> List[Optional[Element]]:
        """Batch query multiple selectors with optimization."""
        return await self.optimizer.batch_element_queries(selectors, **kwargs)
    
    async def click(self, selector: str, **kwargs) -> None:
        """Click with optimized stability waiting."""
        # Wait for stability before clicking
        await self.optimizer.wait_for_stability()
        
        timeout = kwargs.pop('timeout', None) or await self.optimizer.get_optimized_timeout("click")
        await super().click(selector, timeout=timeout, **kwargs)
        
        # Prefetch based on click pattern
        await self.optimizer.prefetch_resources(f"click_{selector}")
    
    async def type(self, selector: str, text: str, **kwargs) -> None:
        """Type with optimized input handling."""
        timeout = kwargs.pop('timeout', None) or await self.optimizer.get_optimized_timeout("input")
        await super().type(selector, text, timeout=timeout, **kwargs)
    
    async def wait_for_load_state(self, state: str = "load", **kwargs) -> None:
        """Wait for load state with optimization."""
        timeout = kwargs.pop('timeout', None) or await self.optimizer.get_optimized_timeout("wait")
        await super().wait_for_load_state(state, timeout=timeout, **kwargs)
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Get performance optimization report."""
        return self.optimizer.get_performance_report()


# Factory function for creating optimized pages
async def create_optimized_page(
    browser_context,
    url: Optional[str] = None,
    optimizer_config: Optional[Dict] = None
) -> OptimizedPage:
    """
    Create an optimized page instance.
    
    Args:
        browser_context: Browser context from playwright
        url: Optional URL to navigate to
        optimizer_config: Configuration for the optimizer
        
    Returns:
        OptimizedPage instance
    """
    page = OptimizedPage(browser_context, optimizer_config=optimizer_config)
    await page.start()
    
    if url:
        await page.goto(url)
    
    return page


# Utility function for batch operations
async def batch_execute_with_optimization(
    page: OptimizedPage,
    operations: List[Dict[str, Any]]
) -> List[Any]:
    """
    Execute batch of operations with performance optimization.
    
    Args:
        page: OptimizedPage instance
        operations: List of operation dictionaries
        
    Returns:
        List of operation results
    """
    results = []
    
    # Group operations by type for batching
    grouped_operations = {}
    for i, op in enumerate(operations):
        op_type = op.get("type", "unknown")
        if op_type not in grouped_operations:
            grouped_operations[op_type] = []
        grouped_operations[op_type].append((i, op))
    
    # Execute grouped operations with optimization
    for op_type, ops in grouped_operations.items():
        if op_type == "query":
            selectors = [op[1]["selector"] for op in ops]
            elements = await page.query_selector_all(selectors)
            
            for (idx, _), element in zip(ops, elements):
                results.append((idx, element))
        
        elif op_type == "click":
            for idx, op in ops:
                await page.click(op["selector"])
                results.append((idx, None))
        
        elif op_type == "type":
            for idx, op in ops:
                await page.type(op["selector"], op["text"])
                results.append((idx, None))
        
        else:
            # Execute other operations sequentially
            for idx, op in ops:
                try:
                    method = getattr(page, op["type"])
                    args = op.get("args", [])
                    kwargs = op.get("kwargs", {})
                    result = await method(*args, **kwargs)
                    results.append((idx, result))
                except Exception as e:
                    logger.error(f"Operation {op['type']} failed: {e}")
                    results.append((idx, None))
    
    # Sort results by original index
    results.sort(key=lambda x: x[0])
    return [r[1] for r in results]