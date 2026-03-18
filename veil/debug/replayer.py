"""
Visual Debugging & Replay System for veil.

Records and replays automation sessions with visual annotations,
making debugging 10x faster. Includes time-travel debugging and
step-by-step visualization.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple
import threading
from enum import Enum

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False

from veil.actor.page import Page
from veil.agent.views import AgentState, AgentStep
from veil.agent.service import Agent


class RecordingState(Enum):
    """State of the recording session."""
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    REPLAYING = "replaying"


@dataclass
class NetworkRequest:
    """Recorded network request."""
    url: str
    method: str
    status: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    response_body: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class DOMSnapshot:
    """DOM snapshot at a specific point."""
    html: str
    url: str
    title: str
    timestamp: float = field(default_factory=time.time)
    viewport_width: int = 1280
    viewport_height: int = 720


@dataclass
class StepAnnotation:
    """Visual annotation for a step."""
    element_selector: Optional[str] = None
    element_text: Optional[str] = None
    action_type: Optional[str] = None
    coordinates: Optional[Tuple[int, int]] = None
    color: str = "#FF0000"
    label: Optional[str] = None


@dataclass
class RecordedStep:
    """Complete recording of a single automation step."""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    step_number: int = 0
    timestamp: float = field(default_factory=time.time)
    
    # State before action
    pre_state: Optional[Dict[str, Any]] = None
    pre_dom: Optional[DOMSnapshot] = None
    pre_screenshot: Optional[str] = None  # base64 encoded
    
    # Action details
    action: Optional[Dict[str, Any]] = None
    annotation: Optional[StepAnnotation] = None
    
    # State after action
    post_state: Optional[Dict[str, Any]] = None
    post_dom: Optional[DOMSnapshot] = None
    post_screenshot: Optional[str] = None  # base64 encoded
    
    # Network activity during step
    network_requests: List[NetworkRequest] = field(default_factory=list)
    
    # Performance metrics
    duration_ms: float = 0.0
    memory_usage_mb: Optional[float] = None
    
    # Debug information
    console_logs: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class RecordingSession:
    """Complete recording session."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    steps: List[RecordedStep] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "steps": [asdict(step) for step in self.steps],
            "metadata": self.metadata,
            "tags": self.tags
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecordingSession":
        """Create from dictionary."""
        session = cls(
            session_id=data.get("session_id", str(uuid.uuid4())),
            start_time=data.get("start_time", time.time()),
            end_time=data.get("end_time"),
            metadata=data.get("metadata", {}),
            tags=data.get("tags", [])
        )
        
        for step_data in data.get("steps", []):
            step = RecordedStep(
                step_id=step_data.get("step_id", str(uuid.uuid4())),
                step_number=step_data.get("step_number", 0),
                timestamp=step_data.get("timestamp", time.time()),
                duration_ms=step_data.get("duration_ms", 0.0),
                memory_usage_mb=step_data.get("memory_usage_mb"),
                console_logs=step_data.get("console_logs", []),
                errors=step_data.get("errors", [])
            )
            
            # Reconstruct nested objects
            if "pre_dom" in step_data and step_data["pre_dom"]:
                step.pre_dom = DOMSnapshot(**step_data["pre_dom"])
            if "post_dom" in step_data and step_data["post_dom"]:
                step.post_dom = DOMSnapshot(**step_data["post_dom"])
            if "annotation" in step_data and step_data["annotation"]:
                step.annotation = StepAnnotation(**step_data["annotation"])
            
            # Network requests
            for req_data in step_data.get("network_requests", []):
                step.network_requests.append(NetworkRequest(**req_data))
            
            session.steps.append(step)
        
        return session


class DebugRecorder:
    """
    Records browser automation sessions with visual annotations.
    
    Captures DOM snapshots, screenshots, and network activity at each step.
    Provides time-travel debugging capabilities.
    """
    
    def __init__(
        self,
        page: Page,
        output_dir: Optional[Union[str, Path]] = None,
        capture_screenshots: bool = True,
        capture_dom: bool = True,
        capture_network: bool = True,
        capture_console: bool = True,
        screenshot_quality: int = 80,
        max_screenshot_dimension: int = 1920,
        annotation_color: str = "#FF0000",
        auto_save_interval: int = 10
    ):
        """
        Initialize the debug recorder.
        
        Args:
            page: The browser page to record
            output_dir: Directory to save recordings
            capture_screenshots: Whether to capture screenshots
            capture_dom: Whether to capture DOM snapshots
            capture_network: Whether to capture network activity
            capture_console: Whether to capture console logs
            screenshot_quality: JPEG quality (1-100)
            max_screenshot_dimension: Max dimension for screenshots
            annotation_color: Color for annotations
            auto_save_interval: Auto-save every N steps
        """
        self.page = page
        self.output_dir = Path(output_dir) if output_dir else Path.home() / ".veil" / "recordings"
        self.capture_screenshots = capture_screenshots
        self.capture_dom = capture_dom
        self.capture_network = capture_network
        self.capture_console = capture_console
        self.screenshot_quality = screenshot_quality
        self.max_screenshot_dimension = max_screenshot_dimension
        self.annotation_color = annotation_color
        self.auto_save_interval = auto_save_interval
        
        self._state = RecordingState.IDLE
        self._current_session: Optional[RecordingSession] = None
        self._current_step: Optional[RecordedStep] = None
        self._step_counter = 0
        self._network_requests: List[NetworkRequest] = []
        self._console_logs: List[Dict[str, str]] = []
        self._lock = threading.RLock()
        self._auto_save_task: Optional[asyncio.Task] = None
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup page event listeners
        self._setup_page_listeners()
    
    def _setup_page_listeners(self):
        """Setup event listeners on the page."""
        if self.capture_network:
            self.page.on("request", self._on_request)
            self.page.on("response", self._on_response)
        
        if self.capture_console:
            self.page.on("console", self._on_console)
            self.page.on("pageerror", self._on_page_error)
    
    async def _on_request(self, request):
        """Handle network request."""
        if self._state != RecordingState.RECORDING:
            return
        
        network_req = NetworkRequest(
            url=request.url,
            method=request.method,
            headers=dict(request.headers) if hasattr(request, 'headers') else {}
        )
        
        with self._lock:
            self._network_requests.append(network_req)
    
    async def _on_response(self, response):
        """Handle network response."""
        if self._state != RecordingState.RECORDING:
            return
        
        # Find matching request
        for req in reversed(self._network_requests):
            if req.url == response.url and req.status is None:
                req.status = response.status
                try:
                    if hasattr(response, 'text'):
                        req.response_body = await response.text()
                except:
                    pass
                break
    
    async def _on_console(self, message):
        """Handle console message."""
        if self._state != RecordingState.RECORDING:
            return
        
        log_entry = {
            "type": message.type,
            "text": message.text,
            "timestamp": time.time()
        }
        
        with self._lock:
            self._console_logs.append(log_entry)
    
    async def _on_page_error(self, error):
        """Handle page error."""
        if self._state != RecordingState.RECORDING:
            return
        
        error_msg = str(error)
        if self._current_step:
            self._current_step.errors.append(error_msg)
    
    async def start_recording(
        self,
        session_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Start recording a new session.
        
        Args:
            session_name: Optional name for the session
            tags: Optional tags for categorization
            metadata: Optional metadata to store with session
            
        Returns:
            Session ID
        """
        if self._state == RecordingState.RECORDING:
            raise RuntimeError("Already recording")
        
        session_id = str(uuid.uuid4())
        self._current_session = RecordingSession(
            session_id=session_id,
            metadata=metadata or {},
            tags=tags or []
        )
        
        if session_name:
            self._current_session.metadata["name"] = session_name
        
        self._step_counter = 0
        self._state = RecordingState.RECORDING
        
        # Start auto-save task
        if self.auto_save_interval > 0:
            self._auto_save_task = asyncio.create_task(self._auto_save_loop())
        
        return session_id
    
    async def stop_recording(self) -> Optional[RecordingSession]:
        """
        Stop recording and save the session.
        
        Returns:
            The recorded session or None if not recording
        """
        if self._state != RecordingState.RECORDING:
            return None
        
        self._state = RecordingState.IDLE
        
        if self._auto_save_task:
            self._auto_save_task.cancel()
            try:
                await self._auto_save_task
            except asyncio.CancelledError:
                pass
        
        if self._current_session:
            self._current_session.end_time = time.time()
            await self._save_session(self._current_session)
            session = self._current_session
            self._current_session = None
            return session
        
        return None
    
    async def record_step(
        self,
        action: Dict[str, Any],
        annotation: Optional[StepAnnotation] = None,
        capture_pre_state: bool = True,
        capture_post_state: bool = True
    ) -> RecordedStep:
        """
        Record a single automation step.
        
        Args:
            action: The action being performed
            annotation: Visual annotation for the action
            capture_pre_state: Whether to capture state before action
            capture_post_state: Whether to capture state after action
            
        Returns:
            The recorded step
        """
        if self._state != RecordingState.RECORDING:
            raise RuntimeError("Not recording")
        
        step_start = time.time()
        self._step_counter += 1
        
        step = RecordedStep(
            step_number=self._step_counter,
            action=action,
            annotation=annotation or StepAnnotation(
                action_type=action.get("type"),
                color=self.annotation_color
            )
        )
        
        # Capture pre-action state
        if capture_pre_state:
            step.pre_state = await self._capture_state()
            if self.capture_dom:
                step.pre_dom = await self._capture_dom_snapshot()
            if self.capture_screenshots:
                step.pre_screenshot = await self._capture_screenshot(annotation)
        
        # Clear network and console logs for this step
        with self._lock:
            self._network_requests.clear()
            self._console_logs.clear()
        
        self._current_step = step
        
        # Return step for post-action capture
        return step
    
    async def finalize_step(self, step: RecordedStep) -> RecordedStep:
        """
        Finalize a step after the action is performed.
        
        Args:
            step: The step to finalize
            
        Returns:
            The finalized step
        """
        if self._state != RecordingState.RECORDING:
            return step
        
        step_end = time.time()
        step.duration_ms = (step_end - step.timestamp) * 1000
        
        # Capture post-action state
        step.post_state = await self._capture_state()
        if self.capture_dom:
            step.post_dom = await self._capture_dom_snapshot()
        if self.capture_screenshots:
            step.post_screenshot = await self._capture_screenshot(step.annotation)
        
        # Capture network and console logs
        with self._lock:
            step.network_requests = self._network_requests.copy()
            step.console_logs = self._console_logs.copy()
        
        # Add to session
        if self._current_session:
            self._current_session.steps.append(step)
        
        # Auto-save if needed
        if (self.auto_save_interval > 0 and 
            self._step_counter % self.auto_save_interval == 0):
            await self._save_session(self._current_session)
        
        self._current_step = None
        
        return step
    
    async def _capture_state(self) -> Dict[str, Any]:
        """Capture current page state."""
        try:
            return {
                "url": self.page.url,
                "title": await self.page.title(),
                "viewport": await self.page.viewport_size(),
                "timestamp": time.time()
            }
        except Exception as e:
            return {"error": str(e), "timestamp": time.time()}
    
    async def _capture_dom_snapshot(self) -> Optional[DOMSnapshot]:
        """Capture DOM snapshot."""
        try:
            html = await self.page.content()
            url = self.page.url
            title = await self.page.title()
            viewport = await self.page.viewport_size()
            
            return DOMSnapshot(
                html=html,
                url=url,
                title=title,
                viewport_width=viewport.get("width", 1280) if viewport else 1280,
                viewport_height=viewport.get("height", 720) if viewport else 720
            )
        except Exception as e:
            print(f"Failed to capture DOM: {e}")
            return None
    
    async def _capture_screenshot(
        self,
        annotation: Optional[StepAnnotation] = None
    ) -> Optional[str]:
        """Capture screenshot with optional annotation."""
        if not PIL_AVAILABLE:
            return None
        
        try:
            # Take screenshot
            screenshot_bytes = await self.page.screenshot(
                type="jpeg",
                quality=self.screenshot_quality
            )
            
            # Convert to PIL Image for annotation
            image = Image.open(io.BytesIO(screenshot_bytes))
            
            # Resize if needed
            if max(image.size) > self.max_screenshot_dimension:
                ratio = self.max_screenshot_dimension / max(image.size)
                new_size = (int(image.width * ratio), int(image.height * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            # Add annotation if provided
            if annotation and (annotation.element_selector or annotation.coordinates):
                image = self._annotate_image(image, annotation)
            
            # Convert to base64
            import io
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=self.screenshot_quality)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
            
        except Exception as e:
            print(f"Failed to capture screenshot: {e}")
            return None
    
    def _annotate_image(self, image: Image.Image, annotation: StepAnnotation) -> Image.Image:
        """Add visual annotation to image."""
        draw = ImageDraw.Draw(image)
        
        # Try to load a font
        try:
            font = ImageFont.truetype("Arial", 20)
        except:
            font = ImageFont.load_default()
        
        if annotation.coordinates:
            x, y = annotation.coordinates
            # Draw circle at coordinates
            radius = 10
            draw.ellipse(
                [(x - radius, y - radius), (x + radius, y + radius)],
                fill=annotation.color,
                outline="white",
                width=2
            )
            
            # Draw label if provided
            if annotation.label:
                draw.text((x + 15, y - 10), annotation.label, fill=annotation.color, font=font)
        
        elif annotation.element_selector:
            # Try to find element and get its bounding box
            # This would require JavaScript execution to get element bounds
            # For now, just add a text label
            text = f"Action: {annotation.action_type or 'unknown'}"
            if annotation.element_text:
                text += f"\nElement: {annotation.element_text[:50]}"
            
            # Draw text background
            text_bbox = draw.textbbox((10, 10), text, font=font)
            draw.rectangle(text_bbox, fill="black", outline=annotation.color)
            draw.text((10, 10), text, fill="white", font=font)
        
        return image
    
    async def _save_session(self, session: RecordingSession):
        """Save session to disk."""
        if not AIOFILES_AVAILABLE:
            # Fallback to synchronous save
            self._save_session_sync(session)
            return
        
        try:
            session_file = self.output_dir / f"{session.session_id}.json"
            
            async with aiofiles.open(session_file, 'w') as f:
                await f.write(json.dumps(session.to_dict(), indent=2))
            
            # Also save as latest
            latest_file = self.output_dir / "latest.json"
            async with aiofiles.open(latest_file, 'w') as f:
                await f.write(json.dumps(session.to_dict(), indent=2))
                
        except Exception as e:
            print(f"Failed to save session: {e}")
    
    def _save_session_sync(self, session: RecordingSession):
        """Synchronous fallback for saving session."""
        try:
            session_file = self.output_dir / f"{session.session_id}.json"
            
            with open(session_file, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)
            
            # Also save as latest
            latest_file = self.output_dir / "latest.json"
            with open(latest_file, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)
                
        except Exception as e:
            print(f"Failed to save session: {e}")
    
    async def _auto_save_loop(self):
        """Auto-save loop for periodic saves."""
        while self._state == RecordingState.RECORDING:
            await asyncio.sleep(60)  # Save every minute
            if self._current_session:
                await self._save_session(self._current_session)
    
    @property
    def state(self) -> RecordingState:
        """Current recording state."""
        return self._state
    
    @property
    def current_session(self) -> Optional[RecordingSession]:
        """Current recording session."""
        return self._current_session


class DebugReplayer:
    """
    Replays recorded automation sessions with visual debugging.
    
    Provides time-travel debugging, step-by-step visualization,
    and interactive timeline UI.
    """
    
    def __init__(
        self,
        session: Optional[Union[RecordingSession, str, Path]] = None,
        recordings_dir: Optional[Union[str, Path]] = None
    ):
        """
        Initialize the replayer.
        
        Args:
            session: Session to replay (or path to session file)
            recordings_dir: Directory containing recordings
        """
        self.recordings_dir = Path(recordings_dir) if recordings_dir else Path.home() / ".veil" / "recordings"
        self._session: Optional[RecordingSession] = None
        self._current_step_index = 0
        self._playback_speed = 1.0
        self._is_playing = False
        self._playback_task: Optional[asyncio.Task] = None
        
        if session:
            self.load_session(session)
    
    def load_session(self, session: Union[RecordingSession, str, Path]) -> RecordingSession:
        """
        Load a session for replay.
        
        Args:
            session: Session object or path to session file
            
        Returns:
            The loaded session
        """
        if isinstance(session, (str, Path)):
            session_path = Path(session)
            if not session_path.exists():
                # Try in recordings directory
                session_path = self.recordings_dir / session
                if not session_path.exists():
                    raise FileNotFoundError(f"Session file not found: {session}")
            
            with open(session_path, 'r') as f:
                data = json.load(f)
                self._session = RecordingSession.from_dict(data)
        else:
            self._session = session
        
        self._current_step_index = 0
        return self._session
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List all available recording sessions.
        
        Returns:
            List of session metadata
        """
        sessions = []
        
        if not self.recordings_dir.exists():
            return sessions
        
        for file in self.recordings_dir.glob("*.json"):
            if file.name == "latest.json":
                continue
            
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                    sessions.append({
                        "session_id": data.get("session_id"),
                        "file": str(file),
                        "start_time": data.get("start_time"),
                        "end_time": data.get("end_time"),
                        "step_count": len(data.get("steps", [])),
                        "tags": data.get("tags", []),
                        "metadata": data.get("metadata", {})
                    })
            except:
                continue
        
        # Sort by start time (newest first)
        sessions.sort(key=lambda x: x.get("start_time", 0), reverse=True)
        return sessions
    
    def get_step(self, step_number: int) -> Optional[RecordedStep]:
        """
        Get a specific step by number.
        
        Args:
            step_number: Step number (1-based)
            
        Returns:
            The step or None if not found
        """
        if not self._session or step_number < 1 or step_number > len(self._session.steps):
            return None
        
        return self._session.steps[step_number - 1]
    
    def get_current_step(self) -> Optional[RecordedStep]:
        """Get the current step."""
        return self.get_step(self._current_step_index + 1)
    
    def next_step(self) -> Optional[RecordedStep]:
        """Move to next step."""
        if not self._session:
            return None
        
        if self._current_step_index < len(self._session.steps) - 1:
            self._current_step_index += 1
        
        return self.get_current_step()
    
    def prev_step(self) -> Optional[RecordedStep]:
        """Move to previous step."""
        if not self._session:
            return None
        
        if self._current_step_index > 0:
            self._current_step_index -= 1
        
        return self.get_current_step()
    
    def go_to_step(self, step_number: int) -> Optional[RecordedStep]:
        """
        Go to a specific step.
        
        Args:
            step_number: Step number (1-based)
            
        Returns:
            The step or None if invalid
        """
        if not self._session or step_number < 1 or step_number > len(self._session.steps):
            return None
        
        self._current_step_index = step_number - 1
        return self.get_current_step()
    
    async def play(
        self,
        start_step: int = 1,
        end_step: Optional[int] = None,
        speed: float = 1.0,
        on_step_callback: Optional[callable] = None
    ):
        """
        Play back the session.
        
        Args:
            start_step: Step to start from (1-based)
            end_step: Step to end at (None for last)
            speed: Playback speed multiplier
            on_step_callback: Callback for each step
        """
        if not self._session:
            raise RuntimeError("No session loaded")
        
        if self._is_playing:
            await self.stop()
        
        self._is_playing = True
        self._playback_speed = speed
        
        start_idx = max(0, start_step - 1)
        end_idx = min(len(self._session.steps) - 1, (end_step - 1) if end_step else len(self._session.steps) - 1)
        
        self._playback_task = asyncio.create_task(
            self._playback_loop(start_idx, end_idx, on_step_callback)
        )
    
    async def _playback_loop(
        self,
        start_idx: int,
        end_idx: int,
        on_step_callback: Optional[callable]
    ):
        """Internal playback loop."""
        try:
            for idx in range(start_idx, end_idx + 1):
                if not self._is_playing:
                    break
                
                self._current_step_index = idx
                step = self._session.steps[idx]
                
                if on_step_callback:
                    await on_step_callback(step, idx + 1)
                
                # Calculate delay based on step duration and playback speed
                delay = step.duration_ms / 1000.0 / self._playback_speed
                await asyncio.sleep(max(0.1, delay))  # Minimum 100ms delay
        
        finally:
            self._is_playing = False
    
    async def stop(self):
        """Stop playback."""
        self._is_playing = False
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
    
    def get_timeline_data(self) -> Dict[str, Any]:
        """
        Get timeline data for visualization.
        
        Returns:
            Timeline data structure
        """
        if not self._session:
            return {}
        
        timeline = {
            "session_id": self._session.session_id,
            "start_time": self._session.start_time,
            "end_time": self._session.end_time,
            "duration": (self._session.end_time or time.time()) - self._session.start_time,
            "step_count": len(self._session.steps),
            "steps": []
        }
        
        for step in self._session.steps:
            timeline["steps"].append({
                "step_number": step.step_number,
                "timestamp": step.timestamp,
                "duration_ms": step.duration_ms,
                "action_type": step.action.get("type") if step.action else None,
                "url": step.pre_state.get("url") if step.pre_state else None,
                "has_screenshot": bool(step.pre_screenshot or step.post_screenshot),
                "has_dom": bool(step.pre_dom or step.post_dom),
                "network_request_count": len(step.network_requests),
                "error_count": len(step.errors)
            })
        
        return timeline
    
    def get_step_diff(self, step_number: int) -> Dict[str, Any]:
        """
        Get diff between pre and post state for a step.
        
        Args:
            step_number: Step number (1-based)
            
        Returns:
            Diff data
        """
        step = self.get_step(step_number)
        if not step:
            return {}
        
        diff = {
            "step_number": step_number,
            "dom_changed": False,
            "url_changed": False,
            "title_changed": False,
            "network_requests": len(step.network_requests),
            "errors": step.errors
        }
        
        if step.pre_dom and step.post_dom:
            diff["dom_changed"] = step.pre_dom.html != step.post_dom.html
            diff["url_changed"] = step.pre_dom.url != step.post_dom.url
            diff["title_changed"] = step.pre_dom.title != step.post_dom.title
        
        return diff
    
    @property
    def session(self) -> Optional[RecordingSession]:
        """Current session."""
        return self._session
    
    @property
    def current_step_index(self) -> int:
        """Current step index (0-based)."""
        return self._current_step_index
    
    @property
    def is_playing(self) -> bool:
        """Whether playback is active."""
        return self._is_playing


class AgentWithDebug(Agent):
    """
    Agent with integrated debugging capabilities.
    
    Extends the base Agent class with automatic recording
    and visual debugging features.
    """
    
    def __init__(
        self,
        *args,
        debug_recorder: Optional[DebugRecorder] = None,
        auto_record: bool = True,
        **kwargs
    ):
        """
        Initialize agent with debugging.
        
        Args:
            debug_recorder: Optional debug recorder instance
            auto_record: Whether to automatically record all steps
            *args, **kwargs: Arguments for base Agent
        """
        super().__init__(*args, **kwargs)
        
        self.debug_recorder = debug_recorder
        self.auto_record = auto_record
        self._current_recorded_step: Optional[RecordedStep] = None
        
        if auto_record and not debug_recorder and hasattr(self, 'page'):
            self.debug_recorder = DebugRecorder(self.page)
    
    async def step(self, *args, **kwargs) -> AgentStep:
        """
        Execute a step with debugging.
        
        Overrides base step method to add recording.
        """
        step_start = time.time()
        
        # Start recording if enabled
        if self.debug_recorder and self.auto_record:
            if self.debug_recorder.state == RecordingState.IDLE:
                await self.debug_recorder.start_recording(
                    session_name=f"agent_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    tags=["agent", "auto"]
                )
            
            # Record pre-state
            action = {
                "type": "agent_step",
                "args": args,
                "kwargs": kwargs,
                "step_number": self.step_number
            }
            
            self._current_recorded_step = await self.debug_recorder.record_step(
                action=action,
                annotation=StepAnnotation(
                    action_type="agent_step",
                    label=f"Step {self.step_number}",
                    color="#00FF00"
                )
            )
        
        # Execute the actual step
        result = await super().step(*args, **kwargs)
        
        # Finalize recording
        if (self.debug_recorder and 
            self.auto_record and 
            self._current_recorded_step):
            
            # Update action with result
            self._current_recorded_step.action["result"] = {
                "success": result.success,
                "data": result.data if hasattr(result, 'data') else None
            }
            
            await self.debug_recorder.finalize_step(self._current_recorded_step)
            self._current_recorded_step = None
        
        return result
    
    async def run(self, *args, **kwargs):
        """
        Run the agent with debugging.
        
        Ensures recording is stopped when agent finishes.
        """
        try:
            return await super().run(*args, **kwargs)
        finally:
            if self.debug_recorder and self.debug_recorder.state == RecordingState.RECORDING:
                await self.debug_recorder.stop_recording()


# Utility functions for quick debugging
async def record_automation(
    page: Page,
    automation_func: callable,
    session_name: Optional[str] = None,
    output_dir: Optional[Union[str, Path]] = None
) -> RecordingSession:
    """
    Quick utility to record an automation function.
    
    Args:
        page: Browser page
        automation_func: Async function to record
        session_name: Optional session name
        output_dir: Optional output directory
        
    Returns:
        Recorded session
    """
    recorder = DebugRecorder(page, output_dir=output_dir)
    
    try:
        session_id = await recorder.start_recording(
            session_name=session_name or f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        
        # Wrap the automation function to record steps
        original_page_methods = {}
        
        async def record_method(method_name, *args, **kwargs):
            method = original_page_methods[method_name]
            
            # Record step
            action = {
                "type": method_name,
                "args": args,
                "kwargs": kwargs
            }
            
            step = await recorder.record_step(action=action)
            
            try:
                # Execute the actual method
                result = await method(*args, **kwargs)
                
                # Update step with result
                step.action["result"] = str(result)[:500] if result else None
                
                return result
            except Exception as e:
                step.errors.append(str(e))
                raise
            finally:
                await recorder.finalize_step(step)
        
        # Monkey-patch page methods to record
        methods_to_record = ['goto', 'click', 'fill', 'type', 'press', 'screenshot']
        for method_name in methods_to_record:
            if hasattr(page, method_name):
                original_method = getattr(page, method_name)
                original_page_methods[method_name] = original_method
                
                # Create recording wrapper
                async def wrapper(*args, _method=method_name, **kwargs):
                    return await record_method(_method, *args, **kwargs)
                
                setattr(page, method_name, wrapper)
        
        # Run the automation
        result = await automation_func(page)
        
        # Restore original methods
        for method_name, original_method in original_page_methods.items():
            setattr(page, method_name, original_method)
        
        # Stop recording
        session = await recorder.stop_recording()
        return session
        
    except Exception as e:
        # Ensure recording is stopped on error
        if recorder.state == RecordingState.RECORDING:
            await recorder.stop_recording()
        raise


def replay_session_interactive(session_path: Optional[Union[str, Path]] = None):
    """
    Interactive session replay in terminal.
    
    Args:
        session_path: Path to session file (optional)
    """
    import sys
    
    replayer = DebugReplayer()
    
    if session_path:
        try:
            session = replayer.load_session(session_path)
            print(f"Loaded session: {session.session_id}")
            print(f"Steps: {len(session.steps)}")
            print(f"Duration: {session.end_time - session.start_time:.2f}s")
        except Exception as e:
            print(f"Error loading session: {e}")
            return
    else:
        sessions = replayer.list_sessions()
        if not sessions:
            print("No sessions found")
            return
        
        print("Available sessions:")
        for i, s in enumerate(sessions, 1):
            print(f"{i}. {s['session_id']} ({s['step_count']} steps)")
        
        try:
            choice = int(input("\nSelect session (number): ")) - 1
            if 0 <= choice < len(sessions):
                session = replayer.load_session(sessions[choice]['file'])
            else:
                print("Invalid choice")
                return
        except (ValueError, KeyboardInterrupt):
            return
    
    print("\nSession loaded. Commands:")
    print("  n/next     - Next step")
    print("  p/prev     - Previous step")
    print("  g <num>    - Go to step")
    print("  t/timeline - Show timeline")
    print("  d <num>    - Show diff for step")
    print("  q/quit     - Quit")
    
    current_step = replayer.get_current_step()
    if current_step:
        print(f"\nStep {current_step.step_number}:")
        print(f"  Action: {current_step.action}")
        print(f"  URL: {current_step.pre_state.get('url') if current_step.pre_state else 'N/A'}")
        print(f"  Duration: {current_step.duration_ms:.2f}ms")
    
    while True:
        try:
            cmd = input("\n> ").strip().lower()
            
            if cmd in ('q', 'quit', 'exit'):
                break
            
            elif cmd in ('n', 'next'):
                step = replayer.next_step()
                if step:
                    print(f"\nStep {step.step_number}:")
                    print(f"  Action: {step.action}")
                    print(f"  URL: {step.pre_state.get('url') if step.pre_state else 'N/A'}")
                    print(f"  Duration: {step.duration_ms:.2f}ms")
                else:
                    print("Already at last step")
            
            elif cmd in ('p', 'prev'):
                step = replayer.prev_step()
                if step:
                    print(f"\nStep {step.step_number}:")
                    print(f"  Action: {step.action}")
                    print(f"  URL: {step.pre_state.get('url') if step.pre_state else 'N/A'}")
                    print(f"  Duration: {step.duration_ms:.2f}ms")
                else:
                    print("Already at first step")
            
            elif cmd.startswith('g '):
                try:
                    step_num = int(cmd[2:])
                    step = replayer.go_to_step(step_num)
                    if step:
                        print(f"\nStep {step.step_number}:")
                        print(f"  Action: {step.action}")
                        print(f"  URL: {step.pre_state.get('url') if step.pre_state else 'N/A'}")
                        print(f"  Duration: {step.duration_ms:.2f}ms")
                    else:
                        print(f"Invalid step number: {step_num}")
                except ValueError:
                    print("Usage: g <step_number>")
            
            elif cmd in ('t', 'timeline'):
                timeline = replayer.get_timeline_data()
                print(f"\nTimeline for session {timeline['session_id']}:")
                print(f"  Total steps: {timeline['step_count']}")
                print(f"  Duration: {timeline['duration']:.2f}s")
                print("\nSteps:")
                for step_data in timeline['steps']:
                    print(f"  {step_data['step_number']:3d}. {step_data['action_type'] or 'N/A':20s} "
                          f"{step_data['duration_ms']:6.1f}ms "
                          f"{'✓' if step_data['error_count'] == 0 else '✗'}")
            
            elif cmd.startswith('d '):
                try:
                    step_num = int(cmd[2:])
                    diff = replayer.get_step_diff(step_num)
                    if diff:
                        print(f"\nDiff for step {step_num}:")
                        print(f"  DOM changed: {diff['dom_changed']}")
                        print(f"  URL changed: {diff['url_changed']}")
                        print(f"  Title changed: {diff['title_changed']}")
                        print(f"  Network requests: {diff['network_requests']}")
                        if diff['errors']:
                            print(f"  Errors: {diff['errors']}")
                    else:
                        print(f"Invalid step number: {step_num}")
                except ValueError:
                    print("Usage: d <step_number>")
            
            else:
                print("Unknown command")
        
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


# Import for screenshot annotation
import io