"""
veil/debug/recorder.py

Visual Debugging & Replay System - Record and replay automation sessions with visual annotations.
Includes time-travel debugging and step-by-step visualization.
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, asdict, field
from enum import Enum

from playwright.async_api import Page, Browser, BrowserContext

from veil.actor.page import Page as ActorPage
from veil.agent.views import AgentStep, AgentState


class RecordingEventType(str, Enum):
    """Types of events that can be recorded."""
    NAVIGATION = "navigation"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    DOM_SNAPSHOT = "dom_snapshot"
    NETWORK_REQUEST = "network_request"
    NETWORK_RESPONSE = "network_response"
    CONSOLE_LOG = "console_log"
    AGENT_STEP = "agent_step"
    AGENT_THINKING = "agent_thinking"
    ERROR = "error"
    CUSTOM = "custom"


@dataclass
class NetworkEvent:
    """Network event data."""
    url: str
    method: str
    status: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    response_time: Optional[float] = None
    request_body: Optional[str] = None
    response_body: Optional[str] = None


@dataclass
class ConsoleEvent:
    """Console event data."""
    level: str
    text: str
    timestamp: float
    location: Optional[str] = None


@dataclass
class RecordingEvent:
    """Single event in the recording."""
    id: str
    timestamp: float
    type: RecordingEventType
    data: Dict[str, Any]
    screenshot: Optional[str] = None  # Base64 encoded
    dom_snapshot: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = asdict(self)
        # Convert enum to string
        result['type'] = self.type.value
        return result


@dataclass
class SessionRecording:
    """Complete session recording."""
    session_id: str
    start_time: float
    end_time: Optional[float] = None
    events: List[RecordingEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    browser_info: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'session_id': self.session_id,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'events': [e.to_dict() for e in self.events],
            'metadata': self.metadata,
            'browser_info': self.browser_info
        }
    
    def save(self, filepath: Union[str, Path]) -> None:
        """Save recording to file."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, filepath: Union[str, Path]) -> 'SessionRecording':
        """Load recording from file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        events = []
        for event_data in data['events']:
            event_data['type'] = RecordingEventType(event_data['type'])
            events.append(RecordingEvent(**event_data))
        
        return cls(
            session_id=data['session_id'],
            start_time=data['start_time'],
            end_time=data.get('end_time'),
            events=events,
            metadata=data.get('metadata', {}),
            browser_info=data.get('browser_info', {})
        )


class SessionRecorder:
    """Records browser automation sessions with visual annotations."""
    
    def __init__(
        self,
        page: Optional[Page] = None,
        actor_page: Optional[ActorPage] = None,
        session_id: Optional[str] = None,
        record_screenshots: bool = True,
        record_dom: bool = True,
        record_network: bool = True,
        record_console: bool = True,
        screenshot_interval: float = 0.5,
        output_dir: str = "./recordings"
    ):
        """
        Initialize the session recorder.
        
        Args:
            page: Playwright page object
            actor_page: Browser-use actor page object
            session_id: Unique session identifier
            record_screenshots: Whether to capture screenshots
            record_dom: Whether to capture DOM snapshots
            record_network: Whether to record network activity
            record_console: Whether to record console logs
            screenshot_interval: Minimum interval between screenshots (seconds)
            output_dir: Directory to save recordings
        """
        self.page = page
        self.actor_page = actor_page
        self.session_id = session_id or f"session_{int(time.time())}"
        self.record_screenshots = record_screenshots
        self.record_dom = record_dom
        self.record_network = record_network
        self.record_console = record_console
        self.screenshot_interval = screenshot_interval
        self.output_dir = Path(output_dir)
        
        self.recording = SessionRecording(
            session_id=self.session_id,
            start_time=time.time()
        )
        
        self._event_counter = 0
        self._last_screenshot_time = 0
        self._network_events: List[NetworkEvent] = []
        self._console_events: List[ConsoleEvent] = []
        self._is_recording = False
        self._setup_done = False
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def start(self) -> None:
        """Start recording the session."""
        if self._is_recording:
            return
        
        self._is_recording = True
        self.recording.start_time = time.time()
        
        # Setup event listeners
        await self._setup_event_listeners()
        
        # Record initial state
        await self.record_event(
            RecordingEventType.CUSTOM,
            {"message": "Recording started"},
            capture_screenshot=True,
            capture_dom=True
        )
        
        print(f"🎬 Recording started: {self.session_id}")
    
    async def stop(self) -> str:
        """
        Stop recording and save to file.
        
        Returns:
            Path to the saved recording file
        """
        if not self._is_recording:
            return ""
        
        self._is_recording = False
        self.recording.end_time = time.time()
        
        # Record final state
        await self.record_event(
            RecordingEventType.CUSTOM,
            {"message": "Recording stopped"},
            capture_screenshot=True,
            capture_dom=True
        )
        
        # Save recording
        filepath = self.output_dir / f"{self.session_id}.json"
        self.recording.save(filepath)
        
        # Generate HTML viewer
        await self._generate_viewer(filepath)
        
        print(f"🎬 Recording stopped: {filepath}")
        return str(filepath)
    
    async def _setup_event_listeners(self) -> None:
        """Setup event listeners for the page."""
        if not self.page or self._setup_done:
            return
        
        # Network events
        if self.record_network:
            self.page.on("request", self._on_request)
            self.page.on("response", self._on_response)
        
        # Console events
        if self.record_console:
            self.page.on("console", self._on_console)
            self.page.on("pageerror", self._on_page_error)
        
        self._setup_done = True
    
    def _on_request(self, request) -> None:
        """Handle network request event."""
        if not self._is_recording:
            return
        
        try:
            network_event = NetworkEvent(
                url=request.url,
                method=request.method,
                headers=dict(request.headers),
                request_body=request.post_data
            )
            self._network_events.append(network_event)
            
            asyncio.create_task(self.record_event(
                RecordingEventType.NETWORK_REQUEST,
                {
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data
                }
            ))
        except Exception as e:
            print(f"Error recording request: {e}")
    
    def _on_response(self, response) -> None:
        """Handle network response event."""
        if not self._is_recording:
            return
        
        try:
            asyncio.create_task(self.record_event(
                RecordingEventType.NETWORK_RESPONSE,
                {
                    "url": response.url,
                    "status": response.status,
                    "headers": dict(response.headers),
                    "request_method": response.request.method
                }
            ))
        except Exception as e:
            print(f"Error recording response: {e}")
    
    def _on_console(self, message) -> None:
        """Handle console message event."""
        if not self._is_recording:
            return
        
        try:
            console_event = ConsoleEvent(
                level=message.type,
                text=message.text,
                timestamp=time.time(),
                location=f"{message.location.get('url', '')}:{message.location.get('lineNumber', '')}"
            )
            self._console_events.append(console_event)
            
            asyncio.create_task(self.record_event(
                RecordingEventType.CONSOLE_LOG,
                {
                    "level": message.type,
                    "text": message.text,
                    "location": console_event.location
                }
            ))
        except Exception as e:
            print(f"Error recording console: {e}")
    
    def _on_page_error(self, error) -> None:
        """Handle page error event."""
        if not self._is_recording:
            return
        
        asyncio.create_task(self.record_event(
            RecordingEventType.ERROR,
            {
                "message": str(error),
                "stack": getattr(error, 'stack', '')
            },
            capture_screenshot=True
        ))
    
    async def record_event(
        self,
        event_type: RecordingEventType,
        data: Dict[str, Any],
        capture_screenshot: bool = False,
        capture_dom: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RecordingEvent:
        """
        Record a single event.
        
        Args:
            event_type: Type of event
            data: Event data
            capture_screenshot: Whether to capture screenshot
            capture_dom: Whether to capture DOM snapshot
            metadata: Additional metadata
            
        Returns:
            The recorded event
        """
        if not self._is_recording:
            raise RuntimeError("Recording not started")
        
        self._event_counter += 1
        event_id = f"event_{self._event_counter:06d}"
        
        # Capture screenshot if requested and interval has passed
        screenshot = None
        if capture_screenshot and self.record_screenshots:
            current_time = time.time()
            if current_time - self._last_screenshot_time >= self.screenshot_interval:
                screenshot = await self._capture_screenshot()
                self._last_screenshot_time = current_time
        
        # Capture DOM if requested
        dom_snapshot = None
        if capture_dom and self.record_dom:
            dom_snapshot = await self._capture_dom()
        
        # Create event
        event = RecordingEvent(
            id=event_id,
            timestamp=time.time(),
            type=event_type,
            data=data,
            screenshot=screenshot,
            dom_snapshot=dom_snapshot,
            metadata=metadata or {}
        )
        
        self.recording.events.append(event)
        return event
    
    async def record_agent_step(self, step: AgentStep, state: AgentState) -> RecordingEvent:
        """Record an agent step with full context."""
        data = {
            "step_number": step.step_number,
            "action": step.action,
            "thought": step.thought,
            "observation": step.observation,
            "goal": state.goal if state else None,
            "current_url": step.url if hasattr(step, 'url') else None
        }
        
        return await self.record_event(
            RecordingEventType.AGENT_STEP,
            data,
            capture_screenshot=True,
            capture_dom=True,
            metadata={
                "step_type": "agent_step",
                "success": step.success if hasattr(step, 'success') else None
            }
        )
    
    async def record_agent_thinking(self, thought: str, context: Optional[Dict] = None) -> RecordingEvent:
        """Record agent thinking process."""
        return await self.record_event(
            RecordingEventType.AGENT_THINKING,
            {
                "thought": thought,
                "context": context or {}
            },
            capture_screenshot=False,
            capture_dom=False
        )
    
    async def record_navigation(self, url: str, referrer: Optional[str] = None) -> RecordingEvent:
        """Record navigation event."""
        return await self.record_event(
            RecordingEventType.NAVIGATION,
            {
                "url": url,
                "referrer": referrer,
                "timestamp": datetime.now().isoformat()
            },
            capture_screenshot=True,
            capture_dom=True
        )
    
    async def record_click(self, selector: str, coordinates: Optional[Dict] = None) -> RecordingEvent:
        """Record click event."""
        return await self.record_event(
            RecordingEventType.CLICK,
            {
                "selector": selector,
                "coordinates": coordinates,
                "element_info": await self._get_element_info(selector) if selector else None
            },
            capture_screenshot=True,
            capture_dom=False
        )
    
    async def record_type(self, selector: str, text: str) -> RecordingEvent:
        """Record typing event."""
        return await self.record_event(
            RecordingEventType.TYPE,
            {
                "selector": selector,
                "text": text,
                "element_info": await self._get_element_info(selector) if selector else None
            },
            capture_screenshot=True,
            capture_dom=False
        )
    
    async def record_scroll(self, x: int, y: int) -> RecordingEvent:
        """Record scroll event."""
        return await self.record_event(
            RecordingEventType.SCROLL,
            {
                "x": x,
                "y": y,
                "page_width": await self._get_page_dimension("width"),
                "page_height": await self._get_page_dimension("height")
            },
            capture_screenshot=True,
            capture_dom=False
        )
    
    async def _capture_screenshot(self) -> Optional[str]:
        """Capture screenshot and return as base64 string."""
        if not self.page:
            return None
        
        try:
            screenshot_bytes = await self.page.screenshot(
                type="jpeg",
                quality=70,
                full_page=False
            )
            return base64.b64encode(screenshot_bytes).decode('utf-8')
        except Exception as e:
            print(f"Error capturing screenshot: {e}")
            return None
    
    async def _capture_dom(self) -> Optional[str]:
        """Capture DOM snapshot."""
        if not self.page:
            return None
        
        try:
            return await self.page.content()
        except Exception as e:
            print(f"Error capturing DOM: {e}")
            return None
    
    async def _get_element_info(self, selector: str) -> Optional[Dict]:
        """Get information about an element."""
        if not self.page or not selector:
            return None
        
        try:
            element = await self.page.query_selector(selector)
            if not element:
                return None
            
            return {
                "tag": await element.evaluate("el => el.tagName.toLowerCase()"),
                "text": await element.evaluate("el => el.innerText?.substring(0, 100)"),
                "attributes": await element.evaluate("""el => {
                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    return attrs;
                }"""),
                "bounding_box": await element.bounding_box()
            }
        except Exception:
            return None
    
    async def _get_page_dimension(self, dimension: str) -> Optional[int]:
        """Get page dimension."""
        if not self.page:
            return None
        
        try:
            if dimension == "width":
                return await self.page.evaluate("document.documentElement.scrollWidth")
            else:
                return await self.page.evaluate("document.documentElement.scrollHeight")
        except Exception:
            return None
    
    async def _generate_viewer(self, recording_path: Path) -> None:
        """Generate HTML viewer for the recording."""
        viewer_path = recording_path.with_suffix('.html')
        
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Session Recording: {self.session_id}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #0f0f0f;
            color: #e0e0e0;
            line-height: 1.6;
        }}
        
        .container {{
            display: grid;
            grid-template-columns: 300px 1fr;
            height: 100vh;
        }}
        
        .sidebar {{
            background: #1a1a1a;
            border-right: 1px solid #333;
            overflow-y: auto;
            padding: 20px;
        }}
        
        .main {{
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        
        .header {{
            background: #1a1a1a;
            padding: 20px;
            border-bottom: 1px solid #333;
        }}
        
        .timeline {{
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        
        .timeline-controls {{
            display: flex;
            gap: 10px;
            padding: 15px 20px;
            background: #252525;
            border-bottom: 1px solid #333;
        }}
        
        .timeline-view {{
            flex: 1;
            overflow-y: auto;
            padding: 20px;
        }}
        
        .event {{
            background: #252525;
            border: 1px solid #333;
            border-radius: 8px;
            margin-bottom: 15px;
            overflow: hidden;
            transition: all 0.2s;
        }}
        
        .event:hover {{
            border-color: #4a9eff;
        }}
        
        .event.active {{
            border-color: #4a9eff;
            box-shadow: 0 0 0 2px rgba(74, 158, 255, 0.2);
        }}
        
        .event-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 15px;
            background: #2a2a2a;
            cursor: pointer;
        }}
        
        .event-type {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-weight: 600;
            font-size: 14px;
        }}
        
        .event-type-icon {{
            width: 20px;
            height: 20px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
        }}
        
        .event-type-navigation .event-type-icon {{ background: #4a9eff; }}
        .event-type-click .event-type-icon {{ background: #4caf50; }}
        .event-type-type .event-type-icon {{ background: #ff9800; }}
        .event-type-agent_step .event-type-icon {{ background: #9c27b0; }}
        .event-type-error .event-type-icon {{ background: #f44336; }}
        
        .event-time {{
            font-size: 12px;
            color: #888;
        }}
        
        .event-body {{
            padding: 15px;
            display: none;
        }}
        
        .event.expanded .event-body {{
            display: block;
        }}
        
        .event-content {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }}
        
        .event-screenshot {{
            border-radius: 6px;
            overflow: hidden;
            border: 1px solid #333;
        }}
        
        .event-screenshot img {{
            width: 100%;
            height: auto;
            display: block;
        }}
        
        .event-details {{
            font-size: 13px;
        }}
        
        .event-details pre {{
            background: #1a1a1a;
            padding: 10px;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 12px;
            max-height: 200px;
        }}
        
        .event-details .label {{
            font-weight: 600;
            color: #888;
            margin-bottom: 5px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .event-details .value {{
            margin-bottom: 10px;
        }}
        
        .btn {{
            background: #333;
            color: #e0e0e0;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            transition: background 0.2s;
        }}
        
        .btn:hover {{
            background: #444;
        }}
        
        .btn-primary {{
            background: #4a9eff;
        }}
        
        .btn-primary:hover {{
            background: #3a8eef;
        }}
        
        .stats {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 20px;
        }}
        
        .stat {{
            background: #252525;
            padding: 15px;
            border-radius: 6px;
            text-align: center;
        }}
        
        .stat-value {{
            font-size: 24px;
            font-weight: 600;
            color: #4a9eff;
        }}
        
        .stat-label {{
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .filter {{
            margin-bottom: 20px;
        }}
        
        .filter input {{
            width: 100%;
            padding: 10px;
            background: #252525;
            border: 1px solid #333;
            border-radius: 4px;
            color: #e0e0e0;
            font-size: 13px;
        }}
        
        .filter input:focus {{
            outline: none;
            border-color: #4a9eff;
        }}
        
        .event-list {{
            max-height: calc(100vh - 300px);
            overflow-y: auto;
        }}
        
        .event-item {{
            padding: 10px 15px;
            border-bottom: 1px solid #333;
            cursor: pointer;
            transition: background 0.2s;
        }}
        
        .event-item:hover {{
            background: #252525;
        }}
        
        .event-item.active {{
            background: #2a3a4a;
            border-left: 3px solid #4a9eff;
        }}
        
        .event-item-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
        }}
        
        .event-item-type {{
            font-weight: 600;
            font-size: 13px;
        }}
        
        .event-item-time {{
            font-size: 11px;
            color: #888;
        }}
        
        .event-item-preview {{
            font-size: 12px;
            color: #888;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        .speed-control {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .speed-control label {{
            font-size: 12px;
            color: #888;
        }}
        
        .speed-control input {{
            width: 100px;
        }}
        
        .timeline-slider {{
            flex: 1;
            margin: 0 20px;
        }}
        
        .timeline-slider input {{
            width: 100%;
        }}
        
        .time-display {{
            font-size: 12px;
            color: #888;
            min-width: 100px;
            text-align: right;
        }}
        
        .dom-viewer {{
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 6px;
            padding: 15px;
            margin-top: 15px;
            max-height: 300px;
            overflow: auto;
        }}
        
        .dom-viewer pre {{
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        
        @media (max-width: 1024px) {{
            .container {{
                grid-template-columns: 1fr;
            }}
            
            .sidebar {{
                display: none;
            }}
            
            .event-content {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <h2 style="margin-bottom: 20px; font-size: 18px;">Session Recording</h2>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-value" id="total-events">0</div>
                    <div class="stat-label">Events</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="duration">0s</div>
                    <div class="stat-label">Duration</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="screenshots">0</div>
                    <div class="stat-label">Screenshots</div>
                </div>
            </div>
            
            <div class="filter">
                <input type="text" id="event-filter" placeholder="Filter events...">
            </div>
            
            <div class="event-list" id="event-list">
                <!-- Events will be populated here -->
            </div>
        </div>
        
        <div class="main">
            <div class="header">
                <h1 style="font-size: 24px; margin-bottom: 10px;">Visual Debug Timeline</h1>
                <p style="color: #888; font-size: 14px;">
                    Session: {self.session_id} • 
                    Recorded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                </p>
            </div>
            
            <div class="timeline">
                <div class="timeline-controls">
                    <button class="btn" id="play-btn">▶ Play</button>
                    <button class="btn" id="pause-btn">⏸ Pause</button>
                    <button class="btn" id="step-back-btn">⏮ Step Back</button>
                    <button class="btn" id="step-forward-btn">⏭ Step Forward</button>
                    
                    <div class="speed-control">
                        <label>Speed:</label>
                        <input type="range" id="speed-slider" min="0.1" max="5" step="0.1" value="1">
                        <span id="speed-value">1x</span>
                    </div>
                    
                    <div class="timeline-slider">
                        <input type="range" id="timeline-slider" min="0" max="100" value="0">
                    </div>
                    
                    <div class="time-display">
                        <span id="current-time">00:00</span> / <span id="total-time">00:00</span>
                    </div>
                </div>
                
                <div class="timeline-view" id="timeline-view">
                    <div id="event-container">
                        <!-- Current event will be displayed here -->
                        <div class="event" id="current-event">
                            <div class="event-header">
                                <div class="event-type">
                                    <span class="event-type-icon">▶</span>
                                    <span id="event-type-text">No event selected</span>
                                </div>
                                <div class="event-time" id="event-time"></div>
                            </div>
                            <div class="event-body">
                                <div class="event-content">
                                    <div class="event-screenshot" id="event-screenshot">
                                        <img src="" alt="Screenshot" id="screenshot-img">
                                    </div>
                                    <div class="event-details" id="event-details">
                                        <!-- Event details will be populated here -->
                                    </div>
                                </div>
                                <div class="dom-viewer" id="dom-viewer" style="display: none;">
                                    <h4 style="margin-bottom: 10px;">DOM Snapshot</h4>
                                    <pre id="dom-content"></pre>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Recording data will be injected here
        const recordingData = {json.dumps(self.recording.to_dict(), indent=2)};
        
        // Initialize the viewer
        document.addEventListener('DOMContentLoaded', function() {{
            initializeViewer(recordingData);
        }});
        
        function initializeViewer(data) {{
            // Populate stats
            document.getElementById('total-events').textContent = data.events.length;
            
            const duration = data.end_time ? data.end_time - data.start_time : 0;
            document.getElementById('duration').textContent = formatTime(duration);
            
            const screenshots = data.events.filter(e => e.screenshot).length;
            document.getElementById('screenshots').textContent = screenshots;
            
            // Set total time
            document.getElementById('total-time').textContent = formatTime(duration);
            
            // Populate event list
            const eventList = document.getElementById('event-list');
            data.events.forEach((event, index) => {{
                const eventItem = document.createElement('div');
                eventItem.className = 'event-item';
                eventItem.dataset.index = index;
                
                const time = new Date(event.timestamp * 1000);
                const timeStr = time.toLocaleTimeString();
                
                let preview = '';
                if (event.type === 'navigation') {{
                    preview = event.data.url || '';
                }} else if (event.type === 'click') {{
                    preview = event.data.selector || '';
                }} else if (event.type === 'type') {{
                    preview = `Type: ${{event.data.text?.substring(0, 30)}}...`;
                }} else if (event.type === 'agent_step') {{
                    preview = event.data.thought?.substring(0, 50) || '';
                }}
                
                eventItem.innerHTML = `
                    <div class="event-item-header">
                        <div class="event-item-type">${{event.type}}</div>
                        <div class="event-item-time">${{timeStr}}</div>
                    </div>
                    <div class="event-item-preview">${{preview}}</div>
                `;
                
                eventItem.addEventListener('click', () => {{
                    showEvent(index);
                }});
                
                eventList.appendChild(eventItem);
            }});
            
            // Show first event
            if (data.events.length > 0) {{
                showEvent(0);
            }}
            
            // Setup controls
            setupControls(data);
        }}
        
        function showEvent(index) {{
            const data = recordingData;
            const event = data.events[index];
            
            // Update active state in list
            document.querySelectorAll('.event-item').forEach(item => {{
                item.classList.remove('active');
            }});
            document.querySelector(`.event-item[data-index="${{index}}"]`)?.classList.add('active');
            
            // Update current event display
            const eventElement = document.getElementById('current-event');
            eventElement.classList.add('active');
            
            // Update event type
            const typeText = document.getElementById('event-type-text');
            typeText.textContent = formatEventType(event.type);
            
            // Update event time
            const eventTime = document.getElementById('event-time');
            const time = new Date(event.timestamp * 1000);
            eventTime.textContent = time.toLocaleTimeString();
            
            // Update screenshot
            const screenshotImg = document.getElementById('screenshot-img');
            const screenshotContainer = document.getElementById('event-screenshot');
            if (event.screenshot) {{
                screenshotImg.src = `data:image/jpeg;base64,${{event.screenshot}}`;
                screenshotContainer.style.display = 'block';
            }} else {{
                screenshotContainer.style.display = 'none';
            }}
            
            // Update details
            const detailsContainer = document.getElementById('event-details');
            detailsContainer.innerHTML = formatEventDetails(event);
            
            // Update DOM viewer
            const domViewer = document.getElementById('dom-viewer');
            const domContent = document.getElementById('dom-content');
            if (event.dom_snapshot) {{
                domContent.textContent = event.dom_snapshot.substring(0, 1000) + (event.dom_snapshot.length > 1000 ? '...' : '');
                domViewer.style.display = 'block';
            }} else {{
                domViewer.style.display = 'none';
            }}
            
            // Update timeline slider
            const slider = document.getElementById('timeline-slider');
            slider.value = (index / (data.events.length - 1)) * 100;
            
            // Update current time display
            const currentTime = document.getElementById('current-time');
            const eventTimeInSeconds = event.timestamp - data.start_time;
            currentTime.textContent = formatTime(eventTimeInSeconds);
        }}
        
        function formatEventType(type) {{
            const typeMap = {{
                'navigation': '🌐 Navigation',
                'click': '👆 Click',
                'type': '⌨️ Type',
                'scroll': '📜 Scroll',
                'screenshot': '📸 Screenshot',
                'dom_snapshot': '📄 DOM Snapshot',
                'network_request': '🌐 Network Request',
                'network_response': '🌐 Network Response',
                'console_log': '💻 Console Log',
                'agent_step': '🤖 Agent Step',
                'agent_thinking': '💭 Agent Thinking',
                'error': '❌ Error',
                'custom': '📝 Custom'
            }};
            return typeMap[type] || type;
        }}
        
        function formatEventDetails(event) {{
            let html = '';
            
            // Add type-specific details
            if (event.type === 'navigation') {{
                html += `
                    <div class="label">URL</div>
                    <div class="value">${{event.data.url || 'N/A'}}</div>
                    <div class="label">Referrer</div>
                    <div class="value">${{event.data.referrer || 'None'}}</div>
                `;
            }} else if (event.type === 'click') {{
                html += `
                    <div class="label">Selector</div>
                    <div class="value">${{event.data.selector || 'N/A'}}</div>
                `;
                if (event.data.coordinates) {{
                    html += `
                        <div class="label">Coordinates</div>
                        <div class="value">X: ${{event.data.coordinates.x}}, Y: ${{event.data.coordinates.y}}</div>
                    `;
                }}
            }} else if (event.type === 'type') {{
                html += `
                    <div class="label">Selector</div>
                    <div class="value">${{event.data.selector || 'N/A'}}</div>
                    <div class="label">Text</div>
                    <div class="value">${{event.data.text || 'N/A'}}</div>
                `;
            }} else if (event.type === 'agent_step') {{
                html += `
                    <div class="label">Step</div>
                    <div class="value">${{event.data.step_number || 'N/A'}}</div>
                    <div class="label">Thought</div>
                    <div class="value">${{event.data.thought || 'N/A'}}</div>
                    <div class="label">Action</div>
                    <div class="value">${{event.data.action || 'N/A'}}</div>
                `;
            }} else if (event.type === 'error') {{
                html += `
                    <div class="label">Error Message</div>
                    <div class="value">${{event.data.message || 'N/A'}}</div>
                `;
                if (event.data.stack) {{
                    html += `
                        <div class="label">Stack Trace</div>
                        <pre>${{event.data.stack}}</pre>
                    `;
                }}
            }}
            
            // Add metadata
            if (Object.keys(event.metadata).length > 0) {{
                html += `
                    <div class="label">Metadata</div>
                    <pre>${{JSON.stringify(event.metadata, null, 2)}}</pre>
                `;
            }}
            
            return html;
        }}
        
        function formatTime(seconds) {{
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return `${{mins.toString().padStart(2, '0')}}:${{secs.toString().padStart(2, '0')}}`;
        }}
        
        function setupControls(data) {{
            let isPlaying = false;
            let currentEventIndex = 0;
            let playInterval = null;
            let playSpeed = 1;
            
            const playBtn = document.getElementById('play-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stepBackBtn = document.getElementById('step-back-btn');
            const stepForwardBtn = document.getElementById('step-forward-btn');
            const speedSlider = document.getElementById('speed-slider');
            const speedValue = document.getElementById('speed-value');
            const timelineSlider = document.getElementById('timeline-slider');
            
            playBtn.addEventListener('click', () => {{
                if (!isPlaying) {{
                    isPlaying = true;
                    playBtn.textContent = '▶ Playing...';
                    
                    playInterval = setInterval(() => {{
                        if (currentEventIndex < data.events.length - 1) {{
                            currentEventIndex++;
                            showEvent(currentEventIndex);
                        }} else {{
                            clearInterval(playInterval);
                            isPlaying = false;
                            playBtn.textContent = '▶ Play';
                        }}
                    }}, 1000 / playSpeed);
                }}
            }});
            
            pauseBtn.addEventListener('click', () => {{
                if (isPlaying) {{
                    clearInterval(playInterval);
                    isPlaying = false;
                    playBtn.textContent = '▶ Play';
                }}
            }});
            
            stepBackBtn.addEventListener('click', () => {{
                if (currentEventIndex > 0) {{
                    currentEventIndex--;
                    showEvent(currentEventIndex);
                }}
            }});
            
            stepForwardBtn.addEventListener('click', () => {{
                if (currentEventIndex < data.events.length - 1) {{
                    currentEventIndex++;
                    showEvent(currentEventIndex);
                }}
            }});
            
            speedSlider.addEventListener('input', (e) => {{
                playSpeed = parseFloat(e.target.value);
                speedValue.textContent = `${{playSpeed}}x`;
                
                if (isPlaying) {{
                    clearInterval(playInterval);
                    playInterval = setInterval(() => {{
                        if (currentEventIndex < data.events.length - 1) {{
                            currentEventIndex++;
                            showEvent(currentEventIndex);
                        }} else {{
                            clearInterval(playInterval);
                            isPlaying = false;
                            playBtn.textContent = '▶ Play';
                        }}
                    }}, 1000 / playSpeed);
                }}
            }});
            
            timelineSlider.addEventListener('input', (e) => {{
                const percentage = parseFloat(e.target.value);
                const newIndex = Math.floor((percentage / 100) * (data.events.length - 1));
                if (newIndex >= 0 && newIndex < data.events.length) {{
                    currentEventIndex = newIndex;
                    showEvent(currentEventIndex);
                }}
            }});
            
            // Filter events
            const filterInput = document.getElementById('event-filter');
            filterInput.addEventListener('input', (e) => {{
                const filter = e.target.value.toLowerCase();
                document.querySelectorAll('.event-item').forEach(item => {{
                    const text = item.textContent.toLowerCase();
                    item.style.display = text.includes(filter) ? 'block' : 'none';
                }});
            }});
        }}
    </script>
</body>
</html>
"""
        
        with open(viewer_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"📊 Viewer generated: {viewer_path}")


class SessionPlayer:
    """Plays back recorded sessions with time-travel debugging."""
    
    def __init__(self, recording_path: Union[str, Path]):
        """
        Initialize the session player.
        
        Args:
            recording_path: Path to the recording file
        """
        self.recording_path = Path(recording_path)
        self.recording = SessionRecording.load(self.recording_path)
        self.current_index = 0
        self.is_playing = False
        self.play_speed = 1.0
        self._play_task = None
    
    def get_event(self, index: int) -> Optional[RecordingEvent]:
        """Get event by index."""
        if 0 <= index < len(self.recording.events):
            return self.recording.events[index]
        return None
    
    def get_current_event(self) -> Optional[RecordingEvent]:
        """Get current event."""
        return self.get_event(self.current_index)
    
    def next_event(self) -> Optional[RecordingEvent]:
        """Move to next event."""
        if self.current_index < len(self.recording.events) - 1:
            self.current_index += 1
        return self.get_current_event()
    
    def previous_event(self) -> Optional[RecordingEvent]:
        """Move to previous event."""
        if self.current_index > 0:
            self.current_index -= 1
        return self.get_current_event()
    
    def go_to_event(self, index: int) -> Optional[RecordingEvent]:
        """Go to specific event by index."""
        if 0 <= index < len(self.recording.events):
            self.current_index = index
        return self.get_current_event()
    
    def go_to_time(self, timestamp: float) -> Optional[RecordingEvent]:
        """Go to event closest to timestamp."""
        closest_index = 0
        min_diff = float('inf')
        
        for i, event in enumerate(self.recording.events):
            diff = abs(event.timestamp - timestamp)
            if diff < min_diff:
                min_diff = diff
                closest_index = i
        
        self.current_index = closest_index
        return self.get_current_event()
    
    async def play(self, speed: float = 1.0, callback=None) -> None:
        """
        Play back the recording.
        
        Args:
            speed: Playback speed multiplier
            callback: Async function to call with each event
        """
        self.play_speed = speed
        self.is_playing = True
        
        try:
            for i in range(self.current_index, len(self.recording.events)):
                if not self.is_playing:
                    break
                
                self.current_index = i
                event = self.get_current_event()
                
                if callback:
                    await callback(event, i)
                
                # Calculate delay to next event
                if i < len(self.recording.events) - 1:
                    next_event = self.recording.events[i + 1]
                    delay = (next_event.timestamp - event.timestamp) / speed
                    await asyncio.sleep(max(0.01, delay))
        
        finally:
            self.is_playing = False
    
    def stop(self) -> None:
        """Stop playback."""
        self.is_playing = False
    
    def get_events_by_type(self, event_type: RecordingEventType) -> List[RecordingEvent]:
        """Get all events of a specific type."""
        return [e for e in self.recording.events if e.type == event_type]
    
    def search_events(self, query: str) -> List[RecordingEvent]:
        """Search events by content."""
        results = []
        query_lower = query.lower()
        
        for event in self.recording.events:
            # Search in event data
            event_str = json.dumps(event.data).lower()
            if query_lower in event_str:
                results.append(event)
                continue
            
            # Search in metadata
            metadata_str = json.dumps(event.metadata).lower()
            if query_lower in metadata_str:
                results.append(event)
        
        return results
    
    def export_to_video(self, output_path: str, fps: int = 1) -> None:
        """
        Export recording as video (requires ffmpeg).
        
        Args:
            output_path: Output video file path
            fps: Frames per second
        """
        import tempfile
        import shutil
        from pathlib import Path
        
        # Create temporary directory for frames
        temp_dir = Path(tempfile.mkdtemp())
        
        try:
            # Extract screenshots
            frame_count = 0
            for event in self.recording.events:
                if event.screenshot:
                    frame_path = temp_dir / f"frame_{frame_count:06d}.jpg"
                    with open(frame_path, 'wb') as f:
                        f.write(base64.b64decode(event.screenshot))
                    frame_count += 1
            
            if frame_count == 0:
                print("No screenshots to export")
                return
            
            # Use ffmpeg to create video
            import subprocess
            cmd = [
                'ffmpeg',
                '-y',
                '-framerate', str(fps),
                '-i', str(temp_dir / 'frame_%06d.jpg'),
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                output_path
            ]
            
            subprocess.run(cmd, check=True)
            print(f"Video exported: {output_path}")
        
        finally:
            # Cleanup
            shutil.rmtree(temp_dir)


# Integration with existing codebase
class DebugRecorderIntegration:
    """Integration helpers for existing veil code."""
    
    @staticmethod
    async def wrap_actor_page(actor_page: ActorPage, **kwargs) -> SessionRecorder:
        """
        Wrap an ActorPage with recording capabilities.
        
        Args:
            actor_page: Browser-use ActorPage instance
            **kwargs: Additional arguments for SessionRecorder
            
        Returns:
            SessionRecorder instance
        """
        recorder = SessionRecorder(
            page=actor_page.page,
            actor_page=actor_page,
            **kwargs
        )
        
        # Monkey-patch methods to record events
        original_goto = actor_page.goto
        original_click = actor_page.click
        original_type = actor_page.type
        original_scroll = actor_page.scroll
        
        async def recorded_goto(url: str, **kw):
            await recorder.record_navigation(url)
            return await original_goto(url, **kw)
        
        async def recorded_click(selector: str, **kw):
            await recorder.record_click(selector)
            return await original_click(selector, **kw)
        
        async def recorded_type(selector: str, text: str, **kw):
            await recorder.record_type(selector, text)
            return await original_type(selector, text, **kw)
        
        async def recorded_scroll(x: int, y: int, **kw):
            await recorder.record_scroll(x, y)
            return await original_scroll(x, y, **kw)
        
        actor_page.goto = recorded_goto
        actor_page.click = recorded_click
        actor_page.type = recorded_type
        actor_page.scroll = recorded_scroll
        
        return recorder
    
    @staticmethod
    async def record_agent_session(agent, session_id: str = None, **kwargs) -> SessionRecorder:
        """
        Record an entire agent session.
        
        Args:
            agent: Browser-use Agent instance
            session_id: Session identifier
            **kwargs: Additional arguments for SessionRecorder
            
        Returns:
            SessionRecorder instance
        """
        from veil.agent.service import Agent
        
        if not isinstance(agent, Agent):
            raise ValueError("Expected Agent instance")
        
        recorder = SessionRecorder(
            page=agent.page.page if hasattr(agent, 'page') else None,
            session_id=session_id,
            **kwargs
        )
        
        await recorder.start()
        
        # Hook into agent steps
        original_step = agent.step
        
        async def recorded_step():
            result = await original_step()
            
            if hasattr(agent, 'state') and agent.state.steps:
                last_step = agent.state.steps[-1]
                await recorder.record_agent_step(last_step, agent.state)
            
            return result
        
        agent.step = recorded_step
        
        # Store recorder reference for later cleanup
        agent._debug_recorder = recorder
        
        return recorder


# Convenience functions
async def record_session(page: Page, **kwargs) -> SessionRecorder:
    """
    Quick function to start recording a session.
    
    Args:
        page: Playwright page object
        **kwargs: Additional arguments for SessionRecorder
        
    Returns:
        SessionRecorder instance
    """
    recorder = SessionRecorder(page=page, **kwargs)
    await recorder.start()
    return recorder


def play_recording(recording_path: Union[str, Path]) -> SessionPlayer:
    """
    Quick function to play a recording.
    
    Args:
        recording_path: Path to recording file
        
    Returns:
        SessionPlayer instance
    """
    return SessionPlayer(recording_path)


# Export main classes
__all__ = [
    'SessionRecorder',
    'SessionPlayer',
    'DebugRecorderIntegration',
    'RecordingEvent',
    'SessionRecording',
    'RecordingEventType',
    'record_session',
    'play_recording'
]