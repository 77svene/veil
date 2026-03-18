"""Mouse class for mouse operations."""

import asyncio
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cdp_use.cdp.input.commands import DispatchMouseEventParameters, SynthesizeScrollGestureParameters
    from cdp_use.cdp.input.types import MouseButton

    from veil.browser.session import BrowserSession


class Mouse:
    """Mouse operations for a target with anti-detection features."""

    def __init__(self, browser_session: 'BrowserSession', session_id: str | None = None, target_id: str | None = None):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._session_id = session_id
        self._target_id = target_id
        self._last_x = 0
        self._last_y = 0
        self._movement_history = []
        
        # Anti-detection configuration
        self._human_like = True
        self._min_move_delay = 0.01  # Minimum delay between movements (seconds)
        self._max_move_delay = 0.05  # Maximum delay between movements
        self._min_click_delay = 0.05  # Minimum delay between click down and up
        self._max_click_delay = 0.15  # Maximum delay between click down and up
        self._coordinate_variance = 2  # Pixel variance for clicks (± pixels)

    async def click(self, x: int, y: int, button: 'MouseButton' = 'left', click_count: int = 1) -> None:
        """Click at the specified coordinates with human-like timing and variance."""
        if self._human_like:
            # Add random coordinate variance to avoid perfect clicks
            x_var = x + random.randint(-self._coordinate_variance, self._coordinate_variance)
            y_var = y + random.randint(-self._coordinate_variance, self._coordinate_variance)
            
            # Move to position with human-like movement
            await self.move(x_var, y_var, steps=random.randint(3, 8))
            
            # Random delay before click
            await asyncio.sleep(random.uniform(0.01, 0.03))
        
        # Mouse press
        press_params: 'DispatchMouseEventParameters' = {
            'type': 'mousePressed',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(
            press_params,
            session_id=self._session_id,
        )

        # Human-like delay between press and release
        if self._human_like:
            await asyncio.sleep(random.uniform(self._min_click_delay, self._max_click_delay))
        else:
            await asyncio.sleep(0.01)  # Minimal delay for non-human mode

        # Mouse release
        release_params: 'DispatchMouseEventParameters' = {
            'type': 'mouseReleased',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(
            release_params,
            session_id=self._session_id,
        )

    async def down(self, button: 'MouseButton' = 'left', click_count: int = 1) -> None:
        """Press mouse button down."""
        params: 'DispatchMouseEventParameters' = {
            'type': 'mousePressed',
            'x': self._last_x,
            'y': self._last_y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(
            params,
            session_id=self._session_id,
        )

    async def up(self, button: 'MouseButton' = 'left', click_count: int = 1) -> None:
        """Release mouse button."""
        params: 'DispatchMouseEventParameters' = {
            'type': 'mouseReleased',
            'x': self._last_x,
            'y': self._last_y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(
            params,
            session_id=self._session_id,
        )

    async def move(self, x: int, y: int, steps: int = 1) -> None:
        """Move mouse to the specified coordinates with human-like curves."""
        if steps <= 1 or not self._human_like:
            # Direct movement for single step or non-human mode
            params: 'DispatchMouseEventParameters' = {'type': 'mouseMoved', 'x': x, 'y': y}
            await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)
            self._last_x = x
            self._last_y = y
            return

        # Generate human-like movement curve
        points = self._generate_bezier_curve(
            start=(self._last_x, self._last_y),
            end=(x, y),
            steps=steps
        )

        # Move through each point with variable delays
        for i, (px, py) in enumerate(points):
            # Add slight randomness to each point
            if i < len(points) - 1:  # Don't modify final point
                px += random.uniform(-0.5, 0.5)
                py += random.uniform(-0.5, 0.5)

            params: 'DispatchMouseEventParameters' = {
                'type': 'mouseMoved',
                'x': int(px),
                'y': int(py),
            }
            await self._client.send.Input.dispatchMouseEvent(params, session_id=self._session_id)
            
            # Update last position
            self._last_x = int(px)
            self._last_y = int(py)
            
            # Variable delay between movements
            if i < len(points) - 1:
                delay = random.uniform(self._min_move_delay, self._max_move_delay)
                # Add slight acceleration/deceleration effect
                if i < steps // 3:
                    delay *= 1.2  # Slower start
                elif i > 2 * steps // 3:
                    delay *= 1.2  # Slower end
                await asyncio.sleep(delay)

    async def scroll(self, x: int = 0, y: int = 0, delta_x: int | None = None, delta_y: int | None = None) -> None:
        """Scroll the page with human-like patterns."""
        if not self._session_id:
            raise RuntimeError('Session ID is required for scroll operations')

        # Method 1: Try mouse wheel event (most reliable)
        try:
            # Get viewport dimensions
            layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=self._session_id)
            viewport_width = layout_metrics['layoutViewport']['clientWidth']
            viewport_height = layout_metrics['layoutViewport']['clientHeight']

            # Use provided coordinates or center of viewport
            scroll_x = x if x > 0 else viewport_width / 2
            scroll_y = y if y > 0 else viewport_height / 2

            # Calculate scroll deltas (positive = down/right)
            scroll_delta_x = delta_x or 0
            scroll_delta_y = delta_y or 0

            if self._human_like and (abs(scroll_delta_x) > 50 or abs(scroll_delta_y) > 50):
                # Break large scrolls into multiple smaller, variable scrolls
                scroll_steps = random.randint(2, 4)
                step_delta_x = scroll_delta_x / scroll_steps
                step_delta_y = scroll_delta_y / scroll_steps
                
                for i in range(scroll_steps):
                    # Add variance to each scroll step
                    step_x = step_delta_x * random.uniform(0.8, 1.2)
                    step_y = step_delta_y * random.uniform(0.8, 1.2)
                    
                    # Dispatch mouse wheel event for this step
                    await self._client.send.Input.dispatchMouseEvent(
                        params={
                            'type': 'mouseWheel',
                            'x': scroll_x,
                            'y': scroll_y,
                            'deltaX': step_x,
                            'deltaY': step_y,
                        },
                        session_id=self._session_id,
                    )
                    
                    # Variable delay between scroll steps
                    if i < scroll_steps - 1:
                        await asyncio.sleep(random.uniform(0.05, 0.15))
            else:
                # Single scroll event for small scrolls or non-human mode
                await self._client.send.Input.dispatchMouseEvent(
                    params={
                        'type': 'mouseWheel',
                        'x': scroll_x,
                        'y': scroll_y,
                        'deltaX': scroll_delta_x,
                        'deltaY': scroll_delta_y,
                    },
                    session_id=self._session_id,
                )
            return

        except Exception:
            pass

        # Method 2: Fallback to synthesizeScrollGesture
        try:
            if self._human_like and (abs(delta_x or 0) > 50 or abs(delta_y or 0) > 50):
                # Break large scrolls into multiple smaller gestures
                scroll_steps = random.randint(2, 4)
                step_x = (delta_x or 0) / scroll_steps
                step_y = (delta_y or 0) / scroll_steps
                
                for i in range(scroll_steps):
                    params: 'SynthesizeScrollGestureParameters' = {
                        'x': x,
                        'y': y,
                        'xDistance': step_x * random.uniform(0.8, 1.2),
                        'yDistance': step_y * random.uniform(0.8, 1.2),
                    }
                    await self._client.send.Input.synthesizeScrollGesture(
                        params,
                        session_id=self._session_id,
                    )
                    if i < scroll_steps - 1:
                        await asyncio.sleep(random.uniform(0.05, 0.15))
            else:
                params: 'SynthesizeScrollGestureParameters' = {
                    'x': x,
                    'y': y,
                    'xDistance': delta_x or 0,
                    'yDistance': delta_y or 0,
                }
                await self._client.send.Input.synthesizeScrollGesture(
                    params,
                    session_id=self._session_id,
                )
        except Exception:
            # Method 3: JavaScript fallback
            if self._human_like and (abs(delta_x or 0) > 50 or abs(delta_y or 0) > 50):
                # Break JavaScript scroll into multiple steps
                scroll_steps = random.randint(2, 4)
                step_x = (delta_x or 0) / scroll_steps
                step_y = (delta_y or 0) / scroll_steps
                
                for i in range(scroll_steps):
                    scroll_js = f'window.scrollBy({step_x * random.uniform(0.8, 1.2)}, {step_y * random.uniform(0.8, 1.2)})'
                    await self._client.send.Runtime.evaluate(
                        params={'expression': scroll_js, 'returnByValue': True},
                        session_id=self._session_id,
                    )
                    if i < scroll_steps - 1:
                        await asyncio.sleep(random.uniform(0.05, 0.15))
            else:
                scroll_js = f'window.scrollBy({delta_x or 0}, {delta_y or 0})'
                await self._client.send.Runtime.evaluate(
                    params={'expression': scroll_js, 'returnByValue': True},
                    session_id=self._session_id,
                )

    def _generate_bezier_curve(self, start: tuple, end: tuple, steps: int) -> list:
        """Generate a Bezier curve for human-like mouse movement."""
        # Control points for Bezier curve (add randomness)
        control1 = (
            start[0] + (end[0] - start[0]) * random.uniform(0.2, 0.4) + random.randint(-20, 20),
            start[1] + (end[1] - start[1]) * random.uniform(0.2, 0.4) + random.randint(-20, 20)
        )
        control2 = (
            start[0] + (end[0] - start[0]) * random.uniform(0.6, 0.8) + random.randint(-20, 20),
            start[1] + (end[1] - start[1]) * random.uniform(0.6, 0.8) + random.randint(-20, 20)
        )
        
        points = []
        for i in range(steps + 1):
            t = i / steps
            # Cubic Bezier formula
            x = (1 - t) ** 3 * start[0] + 3 * (1 - t) ** 2 * t * control1[0] + 3 * (1 - t) * t ** 2 * control2[0] + t ** 3 * end[0]
            y = (1 - t) ** 3 * start[1] + 3 * (1 - t) ** 2 * t * control1[1] + 3 * (1 - t) * t ** 2 * control2[1] + t ** 3 * end[1]
            points.append((x, y))
        
        return points

    def set_human_like(self, enabled: bool = True) -> None:
        """Enable or disable human-like behavior."""
        self._human_like = enabled

    def set_timing_variance(self, min_move: float = 0.01, max_move: float = 0.05, 
                           min_click: float = 0.05, max_click: float = 0.15) -> None:
        """Configure timing variance for human-like behavior."""
        self._min_move_delay = min_move
        self._max_move_delay = max_move
        self._min_click_delay = min_click
        self._max_click_delay = max_click

    def set_coordinate_variance(self, variance: int = 2) -> None:
        """Set pixel variance for click coordinates."""
        self._coordinate_variance = variance