"""
Stealth Mode with Anti-Detection
Advanced evasion techniques to bypass bot detection systems (Cloudflare, DataDome, etc.)
Makes AI agents appear as human users with realistic interaction patterns.
"""

import asyncio
import random
import time
import math
import json
import os
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import numpy as np
from playwright.async_api import Page, BrowserContext, Browser

# Import existing modules for integration
from veil.actor.mouse import Mouse
from veil.actor.page import Page as ActorPage
from veil.actor.utils import get_random_user_agent, get_random_viewport


class EvasionTechnique(Enum):
    """Available evasion techniques"""
    FINGERPRINT_RANDOMIZATION = "fingerprint_randomization"
    HUMAN_MOUSE_MOVEMENT = "human_mouse_movement"
    VARIABLE_TYPING = "variable_typing"
    REALISTIC_SCROLLING = "realistic_scrolling"
    PROXY_ROTATION = "proxy_rotation"
    BROWSER_PROFILES = "browser_profiles"
    CANVAS_FINGERPRINTING = "canvas_fingerprinting"
    WEBGL_FINGERPRINTING = "webgl_fingerprinting"
    AUDIO_FINGERPRINTING = "audio_fingerprinting"
    FONT_FINGERPRINTING = "font_fingerprinting"


@dataclass
class StealthConfig:
    """Configuration for stealth mode"""
    enabled_techniques: List[EvasionTechnique] = field(default_factory=lambda: [
        EvasionTechnique.FINGERPRINT_RANDOMIZATION,
        EvasionTechnique.HUMAN_MOUSE_MOVEMENT,
        EvasionTechnique.VARIABLE_TYPING,
        EvasionTechnique.REALISTIC_SCROLLING,
        EvasionTechnique.PROXY_ROTATION,
        EvasionTechnique.BROWSER_PROFILES
    ])
    
    # Mouse movement settings
    mouse_movement_speed: Tuple[float, float] = (0.5, 2.0)  # min, max seconds
    mouse_curve_intensity: float = 0.3  # 0-1, how much curve in movement
    mouse_pause_probability: float = 0.1  # probability of pausing during movement
    
    # Typing settings
    typing_speed_range: Tuple[int, int] = (50, 150)  # characters per minute
    typing_pause_probability: float = 0.05  # probability of pause between keystrokes
    typing_pause_duration: Tuple[float, float] = (0.1, 0.5)  # seconds
    
    # Scrolling settings
    scroll_speed_range: Tuple[float, float] = (0.5, 2.0)  # pixels per millisecond
    scroll_pause_probability: float = 0.2
    scroll_pause_duration: Tuple[float, float] = (0.5, 2.0)  # seconds
    
    # Proxy settings
    proxy_rotation_interval: int = 300  # seconds between proxy rotations
    proxy_list: List[str] = field(default_factory=list)
    
    # Browser profile settings
    profile_directory: str = "./browser_profiles"
    profile_rotation_interval: int = 3600  # seconds between profile rotations
    
    # Fingerprint settings
    fingerprint_noise_level: float = 0.1  # 0-1, amount of noise to add
    canvas_fingerprint_randomization: bool = True
    webgl_fingerprint_randomization: bool = True
    audio_fingerprint_randomization: bool = True
    font_fingerprint_randomization: bool = True
    
    # Detection avoidance
    avoid_cloudflare: bool = True
    avoid_datadome: bool = True
    avoid_perimeterx: bool = True
    avoid_akamai: bool = True


class BrowserFingerprint:
    """Handles browser fingerprint randomization"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        self.fingerprint_cache = {}
        
    async def randomize_fingerprint(self, page: Page) -> Dict[str, Any]:
        """Randomize browser fingerprint to avoid detection"""
        fingerprint = {}
        
        # Randomize user agent
        fingerprint['user_agent'] = get_random_user_agent()
        
        # Randomize viewport
        viewport = get_random_viewport()
        fingerprint['viewport'] = viewport
        
        # Randomize screen dimensions
        screen_width = random.randint(1024, 3840)
        screen_height = random.randint(768, 2160)
        fingerprint['screen'] = {
            'width': screen_width,
            'height': screen_height,
            'availWidth': screen_width,
            'availHeight': screen_height - random.randint(20, 100),
            'colorDepth': random.choice([24, 32]),
            'pixelDepth': random.choice([24, 32])
        }
        
        # Randomize platform
        platforms = ['Win32', 'Win64', 'MacIntel', 'Linux x86_64', 'Linux armv8l']
        fingerprint['platform'] = random.choice(platforms)
        
        # Randomize hardware concurrency
        fingerprint['hardware_concurrency'] = random.choice([2, 4, 8, 12, 16])
        
        # Randomize device memory
        fingerprint['device_memory'] = random.choice([2, 4, 8, 16, 32])
        
        # Randomize timezone
        timezones = [
            'America/New_York', 'America/Los_Angeles', 'Europe/London',
            'Europe/Paris', 'Asia/Tokyo', 'Australia/Sydney'
        ]
        fingerprint['timezone'] = random.choice(timezones)
        
        # Randomize language
        languages = [
            'en-US,en;q=0.9',
            'en-GB,en;q=0.9',
            'fr-FR,fr;q=0.9',
            'de-DE,de;q=0.9',
            'es-ES,es;q=0.9',
            'ja-JP,ja;q=0.9'
        ]
        fingerprint['language'] = random.choice(languages)
        
        # Randomize plugins
        fingerprint['plugins'] = self._generate_random_plugins()
        
        # Apply fingerprint to page
        await self._apply_fingerprint(page, fingerprint)
        
        return fingerprint
    
    def _generate_random_plugins(self) -> List[Dict]:
        """Generate random browser plugins"""
        plugins = []
        plugin_names = [
            'Chrome PDF Plugin',
            'Chrome PDF Viewer',
            'Native Client',
            'Microsoft Edge PDF Plugin',
            'WebKit built-in PDF'
        ]
        
        for i in range(random.randint(2, 5)):
            plugins.append({
                'name': random.choice(plugin_names),
                'filename': f'internal-pdf-{i}',
                'description': 'Portable Document Format'
            })
        
        return plugins
    
    async def _apply_fingerprint(self, page: Page, fingerprint: Dict[str, Any]):
        """Apply fingerprint to the page"""
        
        # Override navigator properties
        await page.add_init_script(f"""
            // Override user agent
            Object.defineProperty(navigator, 'userAgent', {{
                get: () => '{fingerprint['user_agent']}'
            }});
            
            // Override platform
            Object.defineProperty(navigator, 'platform', {{
                get: () => '{fingerprint['platform']}'
            }});
            
            // Override hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {fingerprint['hardware_concurrency']}
            }});
            
            // Override device memory
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {fingerprint['device_memory']}
            }});
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {{
                get: () => {json.dumps(fingerprint['language'].split(','))}
            }});
            
            // Override screen properties
            Object.defineProperty(screen, 'width', {{
                get: () => {fingerprint['screen']['width']}
            }});
            Object.defineProperty(screen, 'height', {{
                get: () => {fingerprint['screen']['height']}
            }});
            Object.defineProperty(screen, 'availWidth', {{
                get: () => {fingerprint['screen']['availWidth']}
            }});
            Object.defineProperty(screen, 'availHeight', {{
                get: () => {fingerprint['screen']['availHeight']}
            }});
            Object.defineProperty(screen, 'colorDepth', {{
                get: () => {fingerprint['screen']['colorDepth']}
            }});
            
            // Override timezone
            const originalDateTimeFormat = Intl.DateTimeFormat;
            Intl.DateTimeFormat = function(...args) {{
                if (args.length === 0 || !args[1] || !args[1].timeZone) {{
                    args[1] = args[1] || {{}};
                    args[1].timeZone = '{fingerprint['timezone']}';
                }}
                return new originalDateTimeFormat(...args);
            }};
            Intl.DateTimeFormat.prototype = originalDateTimeFormat.prototype;
            
            // Randomize WebGL fingerprint
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) {{
                    return 'Intel Inc.';
                }}
                if (parameter === 37446) {{
                    return 'Intel Iris OpenGL Engine';
                }}
                return getParameter.call(this, parameter);
            }};
            
            // Randomize Canvas fingerprint
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                const context = this.getContext('2d');
                if (context) {{
                    // Add slight noise to canvas
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        imageData.data[i] = imageData.data[i] ^ 1;
                    }}
                    context.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.call(this, type);
            }};
            
            // Randomize AudioContext fingerprint
            const originalCreateOscillator = AudioContext.prototype.createOscillator;
            AudioContext.prototype.createOscillator = function() {{
                const oscillator = originalCreateOscillator.call(this);
                const originalConnect = oscillator.connect;
                oscillator.connect = function(destination) {{
                    const gainNode = this.context.createGain();
                    gainNode.gain.value = 0.9999999999999999;
                    originalConnect.call(this, gainNode);
                    return gainNode.connect(destination);
                }};
                return oscillator;
            }};
        """)
        
        # Set viewport
        await page.set_viewport_size({
            'width': fingerprint['viewport']['width'],
            'height': fingerprint['viewport']['height']
        })


class HumanMouseMovement:
    """Simulates human-like mouse movements"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        self.mouse = None
        
    async def initialize(self, page: Page):
        """Initialize mouse movement simulator"""
        self.mouse = page.mouse
        
    async def move_to(self, x: int, y: int, page: Page):
        """Move mouse to target position with human-like movement"""
        if not self.mouse:
            await self.initialize(page)
        
        # Get current mouse position (approximate)
        current_x, current_y = await self._get_current_position(page)
        
        # Calculate movement parameters
        distance = math.sqrt((x - current_x)**2 + (y - current_y)**2)
        steps = max(10, int(distance / 5))  # More steps for longer distances
        
        # Generate bezier curve control points for natural movement
        control_points = self._generate_bezier_control_points(
            current_x, current_y, x, y
        )
        
        # Move along the curve with variable speed
        for i in range(steps + 1):
            t = i / steps
            point = self._calculate_bezier_point(t, control_points)
            
            # Add micro-pauses randomly
            if random.random() < self.config.mouse_pause_probability:
                await asyncio.sleep(random.uniform(0.05, 0.2))
            
            # Move to point
            await self.mouse.move(point[0], point[1])
            
            # Variable delay between moves
            delay = random.uniform(
                self.config.mouse_movement_speed[0] / steps,
                self.config.mouse_movement_speed[1] / steps
            )
            await asyncio.sleep(delay)
    
    async def _get_current_position(self, page: Page) -> Tuple[float, float]:
        """Get current mouse position (approximation)"""
        # Playwright doesn't provide direct access to mouse position
        # We'll use a JavaScript evaluation to get it
        position = await page.evaluate("""
            () => {
                return {
                    x: window.mouseX || window.innerWidth / 2,
                    y: window.mouseY || window.innerHeight / 2
                };
            }
        """)
        return position['x'], position['y']
    
    def _generate_bezier_control_points(
        self, 
        start_x: float, 
        start_y: float, 
        end_x: float, 
        end_y: float
    ) -> List[Tuple[float, float]]:
        """Generate control points for bezier curve movement"""
        # Calculate midpoints
        mid_x = (start_x + end_x) / 2
        mid_y = (start_y + end_y) / 2
        
        # Add randomness to control points
        intensity = self.config.mouse_curve_intensity
        offset_x = random.uniform(-100, 100) * intensity
        offset_y = random.uniform(-100, 100) * intensity
        
        # Create control points
        control1 = (
            start_x + (mid_x - start_x) * 0.3 + offset_x,
            start_y + (mid_y - start_y) * 0.3 + offset_y
        )
        control2 = (
            start_x + (mid_x - start_x) * 0.7 - offset_x,
            start_y + (mid_y - start_y) * 0.7 - offset_y
        )
        
        return [(start_x, start_y), control1, control2, (end_x, end_y)]
    
    def _calculate_bezier_point(
        self, 
        t: float, 
        control_points: List[Tuple[float, float]]
    ) -> Tuple[float, float]:
        """Calculate point on bezier curve at parameter t"""
        # Cubic bezier formula
        t2 = t * t
        t3 = t2 * t
        mt = 1 - t
        mt2 = mt * mt
        mt3 = mt2 * mt
        
        x = (mt3 * control_points[0][0] + 
             3 * mt2 * t * control_points[1][0] + 
             3 * mt * t2 * control_points[2][0] + 
             t3 * control_points[3][0])
        
        y = (mt3 * control_points[0][1] + 
             3 * mt2 * t * control_points[1][1] + 
             3 * mt * t2 * control_points[2][1] + 
             t3 * control_points[3][1])
        
        return x, y
    
    async def click(self, x: int, y: int, page: Page, button: str = "left"):
        """Click at position with human-like movement"""
        await self.move_to(x, y, page)
        
        # Random delay before click
        await asyncio.sleep(random.uniform(0.05, 0.2))
        
        # Click
        await self.mouse.click(x, y, button=button)
        
        # Random delay after click
        await asyncio.sleep(random.uniform(0.1, 0.3))


class VariableTyping:
    """Simulates variable typing speeds and patterns"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        
    async def type_text(self, text: str, page: Page, selector: Optional[str] = None):
        """Type text with variable speed and occasional pauses"""
        if selector:
            await page.click(selector)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        for char in text:
            # Type character
            await page.keyboard.press(char)
            
            # Variable typing speed
            chars_per_minute = random.randint(
                self.config.typing_speed_range[0],
                self.config.typing_speed_range[1]
            )
            delay = 60.0 / (chars_per_minute * 60)  # Convert to seconds per character
            
            # Add randomness to delay
            delay *= random.uniform(0.8, 1.2)
            
            # Occasional pause
            if random.random() < self.config.typing_pause_probability:
                pause = random.uniform(
                    self.config.typing_pause_duration[0],
                    self.config.typing_pause_duration[1]
                )
                await asyncio.sleep(pause)
            else:
                await asyncio.sleep(delay)
    
    async def fill_input(self, selector: str, text: str, page: Page):
        """Fill input field with human-like typing"""
        await page.click(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        
        # Clear existing text
        await page.keyboard.press("Control+a")
        await asyncio.sleep(random.uniform(0.05, 0.1))
        await page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.1, 0.2))
        
        # Type new text
        await self.type_text(text, page)


class RealisticScrolling:
    """Simulates realistic scrolling patterns"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        
    async def scroll_to_element(self, selector: str, page: Page):
        """Scroll to element with realistic pattern"""
        element = await page.query_selector(selector)
        if not element:
            return
        
        # Get element position
        box = await element.bounding_box()
        if not box:
            return
        
        target_y = box['y'] + box['height'] / 2
        
        # Get current scroll position
        current_y = await page.evaluate("window.scrollY")
        
        # Calculate scroll distance
        distance = target_y - current_y
        
        # Scroll in chunks with variable speed
        await self._scroll_distance(distance, page)
    
    async def scroll_distance(self, distance: float, page: Page):
        """Scroll specified distance with realistic pattern"""
        await self._scroll_distance(distance, page)
    
    async def _scroll_distance(self, distance: float, page: Page):
        """Internal scroll implementation"""
        if abs(distance) < 10:
            # Small distance, just scroll directly
            await page.mouse.wheel(0, distance)
            return
        
        # Break scroll into chunks
        chunks = max(3, int(abs(distance) / 100))
        chunk_distance = distance / chunks
        
        for i in range(chunks):
            # Variable scroll speed
            speed = random.uniform(
                self.config.scroll_speed_range[0],
                self.config.scroll_speed_range[1]
            )
            
            # Calculate delay based on speed
            delay = abs(chunk_distance) / (speed * 1000)  # Convert to seconds
            
            # Scroll chunk
            await page.mouse.wheel(0, chunk_distance)
            
            # Pause between chunks
            if i < chunks - 1:  # Don't pause after last chunk
                if random.random() < self.config.scroll_pause_probability:
                    pause = random.uniform(
                        self.config.scroll_pause_duration[0],
                        self.config.scroll_pause_duration[1]
                    )
                    await asyncio.sleep(pause)
                else:
                    await asyncio.sleep(delay)
    
    async def smooth_scroll_to_bottom(self, page: Page):
        """Smoothly scroll to bottom of page"""
        # Get page height
        page_height = await page.evaluate("document.body.scrollHeight")
        viewport_height = await page.evaluate("window.innerHeight")
        
        current_position = 0
        while current_position < page_height - viewport_height:
            # Scroll increment
            increment = random.randint(100, 300)
            current_position += increment
            
            # Scroll
            await page.mouse.wheel(0, increment)
            
            # Variable delay
            delay = random.uniform(0.1, 0.5)
            await asyncio.sleep(delay)
            
            # Occasionally pause longer
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.5, 2.0))


class ProxyManager:
    """Manages proxy rotation for evasion"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        self.current_proxy = None
        self.last_rotation = 0
        self.proxy_index = 0
        
    async def get_proxy(self) -> Optional[Dict[str, str]]:
        """Get current proxy configuration"""
        if not self.config.proxy_list:
            return None
        
        # Check if rotation is needed
        current_time = time.time()
        if (current_time - self.last_rotation > self.config.proxy_rotation_interval or 
            self.current_proxy is None):
            await self.rotate_proxy()
        
        return self.current_proxy
    
    async def rotate_proxy(self):
        """Rotate to next proxy"""
        if not self.config.proxy_list:
            return
        
        # Get next proxy
        proxy_string = self.config.proxy_list[self.proxy_index]
        self.proxy_index = (self.proxy_index + 1) % len(self.config.proxy_list)
        
        # Parse proxy string (format: protocol://user:pass@host:port)
        proxy_config = self._parse_proxy_string(proxy_string)
        
        self.current_proxy = proxy_config
        self.last_rotation = time.time()
        
        return proxy_config
    
    def _parse_proxy_string(self, proxy_string: str) -> Dict[str, str]:
        """Parse proxy string into configuration"""
        # Simple parsing - in production, use a proper URL parser
        if "://" in proxy_string:
            protocol, rest = proxy_string.split("://", 1)
        else:
            protocol = "http"
            rest = proxy_string
        
        if "@" in rest:
            auth, host_port = rest.split("@", 1)
            username, password = auth.split(":", 1)
        else:
            username = password = None
            host_port = rest
        
        if ":" in host_port:
            host, port = host_port.split(":", 1)
        else:
            host = host_port
            port = "8080" if protocol == "http" else "1080"
        
        proxy_config = {
            "server": f"{protocol}://{host}:{port}"
        }
        
        if username and password:
            proxy_config["username"] = username
            proxy_config["password"] = password
        
        return proxy_config
    
    async def apply_to_context(self, context: BrowserContext):
        """Apply proxy to browser context"""
        proxy = await self.get_proxy()
        if proxy:
            # Note: Proxy is typically set when creating the context
            # This is a placeholder for the concept
            pass


class BrowserProfileManager:
    """Manages browser profiles for evasion"""
    
    def __init__(self, config: StealthConfig):
        self.config = config
        self.profile_dir = Path(config.profile_directory)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.current_profile = None
        self.last_rotation = 0
        
    async def get_profile(self) -> Dict[str, Any]:
        """Get browser profile configuration"""
        current_time = time.time()
        
        # Check if rotation is needed
        if (current_time - self.last_rotation > self.config.profile_rotation_interval or 
            self.current_profile is None):
            await self.rotate_profile()
        
        return self.current_profile
    
    async def rotate_profile(self):
        """Rotate to next browser profile"""
        # List existing profiles
        profiles = list(self.profile_dir.glob("*"))
        
        if not profiles:
            # Create a new profile
            profile_name = f"profile_{int(time.time())}"
            profile_path = self.profile_dir / profile_name
            profile_path.mkdir(exist_ok=True)
            
            # Create profile configuration
            self.current_profile = {
                "name": profile_name,
                "path": str(profile_path),
                "user_data_dir": str(profile_path),
                "created_at": time.time()
            }
        else:
            # Use existing profile or create new one
            if random.random() < 0.3:  # 30% chance to create new profile
                profile_name = f"profile_{int(time.time())}"
                profile_path = self.profile_dir / profile_name
                profile_path.mkdir(exist_ok=True)
                
                self.current_profile = {
                    "name": profile_name,
                    "path": str(profile_path),
                    "user_data_dir": str(profile_path),
                    "created_at": time.time()
                }
            else:
                # Use random existing profile
                profile_path = random.choice(profiles)
                self.current_profile = {
                    "name": profile_path.name,
                    "path": str(profile_path),
                    "user_data_dir": str(profile_path),
                    "created_at": profile_path.stat().st_mtime
                }
        
        self.last_rotation = time.time()
        return self.current_profile
    
    async def cleanup_old_profiles(self, max_age_days: int = 7):
        """Clean up old browser profiles"""
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 3600
        
        for profile_path in self.profile_dir.glob("*"):
            if profile_path.is_dir():
                profile_age = current_time - profile_path.stat().st_mtime
                if profile_age > max_age_seconds:
                    import shutil
                    shutil.rmtree(profile_path)


class StealthMode:
    """Main stealth mode controller"""
    
    def __init__(self, config: Optional[StealthConfig] = None):
        self.config = config or StealthConfig()
        
        # Initialize components
        self.fingerprint = BrowserFingerprint(self.config)
        self.mouse_movement = HumanMouseMovement(self.config)
        self.typing = VariableTyping(self.config)
        self.scrolling = RealisticScrolling(self.config)
        self.proxy_manager = ProxyManager(self.config)
        self.profile_manager = BrowserProfileManager(self.config)
        
        # Detection patterns to avoid
        self.detection_patterns = {
            'cloudflare': [
                'cf-browser-verification',
                'cf_chl_opt',
                'challenge-form',
                'cf_captcha_kind'
            ],
            'datadome': [
                'datadome',
                'dd_captcha',
                '_dd_s',
                'captcha'
            ],
            'perimeterx': [
                '_px',
                'px-captcha',
                'perimeterx'
            ],
            'akamai': [
                'akamai',
                'bm_sz',
                'abck'
            ]
        }
    
    async def apply_to_page(self, page: Page) -> Dict[str, Any]:
        """Apply all stealth techniques to a page"""
        results = {}
        
        # Apply fingerprint randomization
        if EvasionTechnique.FINGERPRINT_RANDOMIZATION in self.config.enabled_techniques:
            results['fingerprint'] = await self.fingerprint.randomize_fingerprint(page)
        
        # Initialize mouse movement
        if EvasionTechnique.HUMAN_MOUSE_MOVEMENT in self.config.enabled_techniques:
            await self.mouse_movement.initialize(page)
        
        # Add detection avoidance scripts
        await self._add_detection_avoidance_scripts(page)
        
        # Add human behavior simulation
        await self._add_human_behavior_simulation(page)
        
        return results
    
    async def _add_detection_avoidance_scripts(self, page: Page):
        """Add scripts to avoid detection"""
        scripts = []
        
        # Avoid Cloudflare detection
        if self.config.avoid_cloudflare:
            scripts.append("""
                // Override common Cloudflare detection methods
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // Override permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
                
                // Override plugins length
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                // Override languages length
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            """)
        
        # Avoid DataDome detection
        if self.config.avoid_datadome:
            scripts.append("""
                // Avoid DataDome fingerprinting
                if (window.DataDome) {
                    window.DataDome.captchaCallback = function() {
                        return true;
                    };
                }
                
                // Override common DataDome checks
                Object.defineProperty(document, 'hidden', {
                    get: () => false
                });
                
                Object.defineProperty(document, 'visibilityState', {
                    get: () => 'visible'
                });
            """)
        
        # Avoid PerimeterX detection
        if self.config.avoid_perimeterx:
            scripts.append("""
                // Avoid PerimeterX detection
                if (window._pxAppId) {
                    window._pxAppId = '';
                }
                
                // Override mouse movement tracking
                window._pxMouseMovement = [];
            """)
        
        # Avoid Akamai detection
        if self.config.avoid_akamai:
            scripts.append("""
                // Avoid Akamai Bot Manager
                if (window.bmak) {
                    window.bmak.fov = function() { return 1; };
                    window.bmak ydk = function() { return ''; };
                }
            """)
        
        # Apply all scripts
        for script in scripts:
            await page.add_init_script(script)
    
    async def _add_human_behavior_simulation(self, page: Page):
        """Add scripts to simulate human behavior"""
        
        # Track mouse movement for internal use
        await page.add_init_script("""
            // Track mouse position
            window.mouseX = window.innerWidth / 2;
            window.mouseY = window.innerHeight / 2;
            
            document.addEventListener('mousemove', (e) => {
                window.mouseX = e.clientX;
                window.mouseY = e.clientY;
            });
            
            // Simulate human-like timing
            const originalSetTimeout = window.setTimeout;
            window.setTimeout = function(callback, delay, ...args) {
                // Add slight randomness to timeouts
                const jitter = delay * 0.1 * (Math.random() - 0.5);
                return originalSetTimeout(callback, delay + jitter, ...args);
            };
            
            // Simulate human-like intervals
            const originalSetInterval = window.setInterval;
            window.setInterval = function(callback, delay, ...args) {
                // Add slight randomness to intervals
                const jitter = delay * 0.05 * (Math.random() - 0.5);
                return originalSetInterval(callback, delay + jitter, ...args);
            };
            
            // Override Date.now() to add slight randomness
            const originalDateNow = Date.now;
            Date.now = function() {
                return originalDateNow() + Math.floor(Math.random() * 10);
            };
        """)
    
    async def human_click(self, x: int, y: int, page: Page, button: str = "left"):
        """Perform human-like click"""
        if EvasionTechnique.HUMAN_MOUSE_MOVEMENT in self.config.enabled_techniques:
            await self.mouse_movement.click(x, y, page, button)
        else:
            await page.mouse.click(x, y, button=button)
    
    async def human_type(self, text: str, page: Page, selector: Optional[str] = None):
        """Perform human-like typing"""
        if EvasionTechnique.VARIABLE_TYPING in self.config.enabled_techniques:
            await self.typing.type_text(text, page, selector)
        else:
            if selector:
                await page.fill(selector, text)
            else:
                await page.keyboard.type(text)
    
    async def human_scroll(self, distance: float, page: Page):
        """Perform human-like scrolling"""
        if EvasionTechnique.REALISTIC_SCROLLING in self.config.enabled_techniques:
            await self.scrolling.scroll_distance(distance, page)
        else:
            await page.mouse.wheel(0, distance)
    
    async def check_for_detection(self, page: Page) -> Dict[str, bool]:
        """Check if page contains detection mechanisms"""
        detection_results = {}
        
        for system, patterns in self.detection_patterns.items():
            detected = False
            for pattern in patterns:
                try:
                    element = await page.query_selector(f'[class*="{pattern}"], [id*="{pattern}"], [name*="{pattern}"]')
                    if element:
                        detected = True
                        break
                except:
                    pass
            
            # Also check page content
            if not detected:
                content = await page.content()
                for pattern in patterns:
                    if pattern.lower() in content.lower():
                        detected = True
                        break
            
            detection_results[system] = detected
        
        return detection_results
    
    async def bypass_detection(self, page: Page) -> bool:
        """Attempt to bypass detected protection systems"""
        detections = await self.check_for_detection(page)
        
        for system, detected in detections.items():
            if detected:
                print(f"Detected {system} protection, attempting bypass...")
                
                if system == 'cloudflare':
                    await self._bypass_cloudflare(page)
                elif system == 'datadome':
                    await self._bypass_datadome(page)
                elif system == 'perimeterx':
                    await self._bypass_perimeterx(page)
                elif system == 'akamai':
                    await self._bypass_akamai(page)
        
        return True
    
    async def _bypass_cloudflare(self, page: Page):
        """Attempt to bypass Cloudflare protection"""
        # Wait for challenge to load
        await asyncio.sleep(2)
        
        # Try to find and click verification checkbox
        try:
            checkbox = await page.query_selector('input[type="checkbox"]')
            if checkbox:
                await self.human_click(
                    await checkbox.bounding_box()['x'] + 10,
                    await checkbox.bounding_box()['y'] + 10,
                    page
                )
                await asyncio.sleep(3)
        except:
            pass
        
        # If there's a CAPTCHA, we can't bypass it automatically
        # In production, you might integrate with a CAPTCHA solving service
    
    async def _bypass_datadome(self, page: Page):
        """Attempt to bypass DataDome protection"""
        # DataDome often uses JavaScript challenges
        # Wait for challenge to complete
        await asyncio.sleep(5)
        
        # Check if we passed
        current_url = page.url
        if 'captcha' in current_url.lower():
            print("DataDome CAPTCHA detected - manual intervention required")
    
    async def _bypass_perimeterx(self, page: Page):
        """Attempt to bypass PerimeterX protection"""
        # PerimeterX uses behavioral analysis
        # Simulate human behavior
        await self._simulate_human_behavior(page)
    
    async def _bypass_akamai(self, page: Page):
        """Attempt to bypass Akamai Bot Manager"""
        # Akamai uses fingerprinting and behavioral analysis
        # Already handled by our fingerprint randomization
        pass
    
    async def _simulate_human_behavior(self, page: Page):
        """Simulate human behavior to avoid detection"""
        # Random mouse movements
        for _ in range(random.randint(3, 8)):
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            await self.human_click(x, y, page)
            await asyncio.sleep(random.uniform(0.5, 2.0))
        
        # Random scrolling
        for _ in range(random.randint(2, 5)):
            scroll_distance = random.randint(-300, 300)
            await self.human_scroll(scroll_distance, page)
            await asyncio.sleep(random.uniform(0.5, 1.5))


# Integration with existing codebase
class StealthActorPage(ActorPage):
    """Extended Page class with stealth capabilities"""
    
    def __init__(self, page: Page, stealth_config: Optional[StealthConfig] = None):
        super().__init__(page)
        self.stealth = StealthMode(stealth_config)
        self._stealth_initialized = False
    
    async def initialize_stealth(self):
        """Initialize stealth mode for this page"""
        if not self._stealth_initialized:
            await self.stealth.apply_to_page(self.page)
            self._stealth_initialized = True
    
    async def click(self, selector: str, **kwargs):
        """Click with stealth"""
        await self.initialize_stealth()
        
        # Get element position
        element = await self.page.query_selector(selector)
        if not element:
            raise Exception(f"Element not found: {selector}")
        
        box = await element.bounding_box()
        if not box:
            raise Exception(f"Could not get bounding box for: {selector}")
        
        # Calculate click position
        x = box['x'] + box['width'] / 2
        y = box['y'] + box['height'] / 2
        
        # Human-like click
        await self.stealth.human_click(x, y, self.page)
    
    async def type(self, selector: str, text: str, **kwargs):
        """Type with stealth"""
        await self.initialize_stealth()
        await self.stealth.human_type(text, self.page, selector)
    
    async def scroll(self, distance: float, **kwargs):
        """Scroll with stealth"""
        await self.initialize_stealth()
        await self.stealth.human_scroll(distance, self.page)


# Factory function for creating stealth browser contexts
async def create_stealth_context(
    browser: Browser,
    config: Optional[StealthConfig] = None,
    proxy: Optional[str] = None
) -> BrowserContext:
    """Create a browser context with stealth settings"""
    stealth_config = config or StealthConfig()
    stealth = StealthMode(stealth_config)
    
    # Get proxy configuration
    proxy_config = None
    if proxy:
        proxy_config = stealth.proxy_manager._parse_proxy_string(proxy)
    elif stealth_config.proxy_list:
        proxy_config = await stealth.proxy_manager.get_proxy()
    
    # Get browser profile
    profile = await stealth.profile_manager.get_profile()
    
    # Create context with stealth settings
    context_options = {
        'user_agent': get_random_user_agent(),
        'viewport': get_random_viewport(),
        'ignore_https_errors': True,
        'java_script_enabled': True,
    }
    
    if proxy_config:
        context_options['proxy'] = proxy_config
    
    if profile:
        context_options['user_data_dir'] = profile['user_data_dir']
    
    context = await browser.new_context(**context_options)
    
    # Apply fingerprint randomization to all pages in this context
    await stealth.fingerprint.randomize_fingerprint(await context.new_page())
    
    return context


# Export main classes and functions
__all__ = [
    'StealthMode',
    'StealthConfig',
    'EvasionTechnique',
    'BrowserFingerprint',
    'HumanMouseMovement',
    'VariableTyping',
    'RealisticScrolling',
    'ProxyManager',
    'BrowserProfileManager',
    'StealthActorPage',
    'create_stealth_context'
]