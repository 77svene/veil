"""Stealth Mode with Anti-Detection - Advanced evasion techniques for browser automation.

This module implements comprehensive bot detection bypass for Cloudflare, DataDome,
PerimeterX, and other anti-bot systems. Features include browser fingerprint
randomization, human-like interaction patterns, and proxy rotation integration.
"""

import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
from playwright.async_api import Page, BrowserContext, Browser
import hashlib
import uuid

from veil.actor.mouse import Mouse
from veil.actor.page import Page as BrowserPage


class StealthLevel(Enum):
    """Stealth intensity levels."""
    MINIMAL = "minimal"  # Basic fingerprint spoofing
    STANDARD = "standard"  # Standard evasion techniques
    AGGRESSIVE = "aggressive"  # Maximum evasion, slower performance
    HUMAN = "human"  # Perfect human simulation, slowest


@dataclass
class FingerprintProfile:
    """Complete browser fingerprint profile."""
    user_agent: str
    platform: str
    languages: List[str]
    screen_width: int
    screen_height: int
    color_depth: int
    pixel_ratio: float
    timezone: str
    webgl_vendor: str
    webgl_renderer: str
    canvas_hash: str
    audio_hash: str
    fonts: List[str]
    plugins: List[Dict[str, str]]
    hardware_concurrency: int
    device_memory: int
    touch_support: bool
    do_not_track: Optional[str] = None
    max_touch_points: int = 0
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ProxyConfig:
    """Proxy configuration for rotation."""
    server: str
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    country: Optional[str] = None
    city: Optional[str] = None
    last_used: float = 0
    success_rate: float = 1.0
    latency: float = 0.0


class HumanBehavior:
    """Simulates realistic human interaction patterns."""
    
    @staticmethod
    def bezier_curve(start: Tuple[float, float], 
                     end: Tuple[float, float], 
                     control_points: int = 2) -> List[Tuple[float, float]]:
        """Generate human-like mouse movement using Bezier curves."""
        points = []
        steps = random.randint(20, 40)
        
        # Generate random control points
        controls = []
        for i in range(control_points):
            t = (i + 1) / (control_points + 1)
            # Add randomness to control points
            noise_x = random.gauss(0, 20)
            noise_y = random.gauss(0, 20)
            x = start[0] + (end[0] - start[0]) * t + noise_x
            y = start[1] + (end[1] - start[1]) * t + noise_y
            controls.append((x, y))
        
        # Build complete point list including start, controls, and end
        all_points = [start] + controls + [end]
        
        # Generate Bezier curve points
        for i in range(steps + 1):
            t = i / steps
            point = HumanBehavior._de_casteljau(all_points, t)
            points.append(point)
        
        return points
    
    @staticmethod
    def _de_casteljau(points: List[Tuple[float, float]], t: float) -> Tuple[float, float]:
        """De Casteljau's algorithm for Bezier curves."""
        if len(points) == 1:
            return points[0]
        
        new_points = []
        for i in range(len(points) - 1):
            x = (1 - t) * points[i][0] + t * points[i + 1][0]
            y = (1 - t) * points[i][1] + t * points[i + 1][1]
            new_points.append((x, y))
        
        return HumanBehavior._de_casteljau(new_points, t)
    
    @staticmethod
    def typing_delay(char: str, base_delay: float = 0.1) -> float:
        """Generate realistic typing delays with variance."""
        # Common characters are typed faster
        common_chars = set('etaoinsrhldcumfpgwybvkxjqz ')
        if char.lower() in common_chars:
            delay = base_delay * random.uniform(0.5, 0.9)
        else:
            delay = base_delay * random.uniform(1.0, 1.8)
        
        # Add occasional longer pauses (thinking)
        if random.random() < 0.02:
            delay += random.uniform(0.5, 2.0)
        
        return delay
    
    @staticmethod
    def scroll_pattern(start_y: float, end_y: float, 
                       page_height: float) -> List[Tuple[float, float, float]]:
        """Generate realistic scroll patterns with acceleration/deceleration."""
        points = []
        current_y = start_y
        distance = end_y - start_y
        direction = 1 if distance > 0 else -1
        
        # Scroll in segments with variable speed
        segments = random.randint(3, 8)
        segment_height = abs(distance) / segments
        
        for i in range(segments):
            # Variable scroll speed per segment
            speed = random.uniform(0.3, 1.2)
            steps = int(segment_height / (10 * speed))
            
            for j in range(steps):
                # Ease in/out effect
                progress = j / steps
                ease = 0.5 - math.cos(progress * math.pi) / 2
                y = current_y + direction * segment_height * ease / steps
                
                # Add micro-scrolls (human imperfection)
                if random.random() < 0.1:
                    y += random.gauss(0, 2)
                
                delay = random.uniform(0.01, 0.05)
                points.append((y, delay, speed))
            
            current_y += direction * segment_height
            
            # Occasional pause between segments
            if random.random() < 0.3:
                points.append((current_y, random.uniform(0.2, 1.0), 0))
        
        return points
    
    @staticmethod
    def mouse_jitter(x: float, y: float, radius: float = 2.0) -> Tuple[float, float]:
        """Add subtle mouse jitter for realism."""
        angle = random.uniform(0, 2 * math.pi)
        distance = random.uniform(0, radius)
        jitter_x = x + math.cos(angle) * distance
        jitter_y = y + math.sin(angle) * distance
        return (jitter_x, jitter_y)


class FingerprintGenerator:
    """Generates consistent but randomized browser fingerprints."""
    
    # Realistic user agents by browser and OS
    USER_AGENTS = {
        "chrome_windows": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        ],
        "chrome_mac": [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
        "firefox_windows": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ],
        "safari_mac": [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        ],
    }
    
    # Common screen resolutions
    SCREEN_RESOLUTIONS = [
        (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
        (1280, 720), (1600, 900), (2560, 1440), (1680, 1050),
    ]
    
    # WebGL configurations
    WEBGL_CONFIGS = [
        {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)"},
        {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0)"},
        {"vendor": "Google Inc. (Intel)", "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)"},
    ]
    
    # Common fonts by OS
    FONTS = {
        "windows": [
            "Arial", "Calibri", "Cambria", "Comic Sans MS", "Consolas",
            "Courier New", "Georgia", "Impact", "Lucida Console", "Segoe UI",
            "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana",
        ],
        "mac": [
            "American Typewriter", "Andale Mono", "Arial", "Avenir",
            "Baskerville", "Courier", "Didot", "Futura", "Geneva",
            "Georgia", "Gill Sans", "Helvetica", "Menlo", "Monaco",
            "Optima", "Palatino", "Times", "Times New Roman", "Verdana",
        ],
    }
    
    @classmethod
    def generate_profile(cls, browser_type: str = "chrome", 
                         os_type: str = "windows") -> FingerprintProfile:
        """Generate a complete fingerprint profile."""
        # Select user agent based on browser and OS
        ua_key = f"{browser_type}_{os_type}"
        user_agent = random.choice(cls.USER_AGENTS.get(ua_key, cls.USER_AGENTS["chrome_windows"]))
        
        # Screen resolution
        width, height = random.choice(cls.SCREEN_RESOLUTIONS)
        
        # WebGL configuration
        webgl_config = random.choice(cls.WEBGL_CONFIGS)
        
        # Generate canvas fingerprint hash
        canvas_hash = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()
        
        # Generate audio fingerprint hash
        audio_hash = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()
        
        # Select fonts based on OS
        fonts = random.sample(cls.FONTS.get(os_type, cls.FONTS["windows"]), 
                             k=random.randint(8, 15))
        
        # Generate plugins
        plugins = cls._generate_plugins(browser_type)
        
        return FingerprintProfile(
            user_agent=user_agent,
            platform="Win32" if os_type == "windows" else "MacIntel",
            languages=["en-US", "en"],
            screen_width=width,
            screen_height=height,
            color_depth=24,
            pixel_ratio=random.choice([1, 1.25, 1.5, 2]),
            timezone=random.choice(["America/New_York", "America/Los_Angeles", 
                                   "Europe/London", "Europe/Paris"]),
            webgl_vendor=webgl_config["vendor"],
            webgl_renderer=webgl_config["renderer"],
            canvas_hash=canvas_hash,
            audio_hash=audio_hash,
            fonts=fonts,
            plugins=plugins,
            hardware_concurrency=random.choice([4, 8, 12, 16]),
            device_memory=random.choice([4, 8, 16, 32]),
            touch_support=os_type == "windows" and random.random() < 0.3,
            do_not_track="1" if random.random() < 0.5 else None,
            max_touch_points=10 if random.random() < 0.2 else 0,
        )
    
    @staticmethod
    def _generate_plugins(browser_type: str) -> List[Dict[str, str]]:
        """Generate realistic browser plugins."""
        plugins = []
        
        if browser_type == "chrome":
            plugins.extend([
                {"name": "Chrome PDF Plugin", "filename": "internal-pdf-viewer"},
                {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
                {"name": "Native Client", "filename": "internal-nacl-plugin"},
            ])
        
        if random.random() < 0.7:
            plugins.append({"name": "Widevine Content Decryption Module", 
                          "filename": "widevinecdmadapter.dll"})
        
        return plugins


class StealthMouse(Mouse):
    """Enhanced mouse with human-like movement patterns."""
    
    def __init__(self, page: Page, stealth_level: StealthLevel = StealthLevel.STANDARD):
        super().__init__(page)
        self.stealth_level = stealth_level
        self._last_position = (0, 0)
    
    async def move(self, x: float, y: float, **kwargs) -> None:
        """Move mouse with human-like trajectory."""
        start_x, start_y = self._last_position
        
        # Generate human-like path
        if self.stealth_level in [StealthLevel.AGGRESSIVE, StealthLevel.HUMAN]:
            points = HumanBehavior.bezier_curve((start_x, start_y), (x, y))
            
            # Move through each point with variable speed
            for i, (px, py) in enumerate(points):
                # Add subtle jitter
                if random.random() < 0.3:
                    px, py = HumanBehavior.mouse_jitter(px, py)
                
                await super().move(px, py, **kwargs)
                
                # Variable delay between moves
                delay = random.uniform(0.005, 0.02)
                if i % 5 == 0:  # Occasional longer pause
                    delay += random.uniform(0.05, 0.15)
                await asyncio.sleep(delay)
        else:
            # Simple movement with basic delay
            await super().move(x, y, **kwargs)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        
        self._last_position = (x, y)
    
    async def click(self, x: float, y: float, **kwargs) -> None:
        """Click with human-like preparation."""
        # First move to position
        await self.move(x, y)
        
        # Small jitter before click
        if self.stealth_level in [StealthLevel.AGGRESSIVE, StealthLevel.HUMAN]:
            jitter_x, jitter_y = HumanBehavior.mouse_jitter(x, y, radius=1.0)
            await super().move(jitter_x, jitter_y)
            await asyncio.sleep(random.uniform(0.02, 0.08))
        
        # Perform click with variable delay
        await super().click(x, y, **kwargs)
        
        # Post-click jitter (human imperfection)
        if random.random() < 0.2:
            await asyncio.sleep(random.uniform(0.1, 0.3))


class StealthKeyboard:
    """Enhanced keyboard with realistic typing patterns."""
    
    def __init__(self, page: Page, stealth_level: StealthLevel = StealthLevel.STANDARD):
        self.page = page
        self.stealth_level = stealth_level
    
    async def type(self, text: str, delay: Optional[float] = None, **kwargs) -> None:
        """Type text with human-like delays and occasional mistakes."""
        for i, char in enumerate(text):
            # Calculate delay for this character
            if delay is None:
                char_delay = HumanBehavior.typing_delay(char)
                
                # Adjust based on stealth level
                if self.stealth_level == StealthLevel.MINIMAL:
                    char_delay *= 0.5
                elif self.stealth_level == StealthLevel.HUMAN:
                    char_delay *= 1.5
            else:
                char_delay = delay
            
            # Occasionally make a typo and correct it
            if (self.stealth_level in [StealthLevel.AGGRESSIVE, StealthLevel.HUMAN] and 
                random.random() < 0.01 and i > 0):
                # Type wrong character
                wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
                await self.page.keyboard.press(wrong_char)
                await asyncio.sleep(random.uniform(0.1, 0.3))
                # Backspace and type correct
                await self.page.keyboard.press('Backspace')
                await asyncio.sleep(random.uniform(0.05, 0.15))
            
            # Type the character
            await self.page.keyboard.press(char)
            
            # Apply delay
            await asyncio.sleep(char_delay)
            
            # Occasional pause (thinking)
            if random.random() < 0.005:
                await asyncio.sleep(random.uniform(0.5, 2.0))
    
    async def press(self, key: str, **kwargs) -> None:
        """Press a key with realistic timing."""
        await self.page.keyboard.press(key, **kwargs)
        await asyncio.sleep(random.uniform(0.05, 0.15))


class StealthMode:
    """Main stealth mode controller for anti-detection."""
    
    def __init__(self, 
                 level: StealthLevel = StealthLevel.STANDARD,
                 proxy_pool: Optional[List[ProxyConfig]] = None,
                 profile_dir: Optional[Path] = None):
        self.level = level
        self.proxy_pool = proxy_pool or []
        self.profile_dir = profile_dir or Path.home() / ".veil" / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        
        self._current_profile: Optional[FingerprintProfile] = None
        self._current_proxy: Optional[ProxyConfig] = None
        self._mouse: Optional[StealthMouse] = None
        self._keyboard: Optional[StealthKeyboard] = None
        
        # Load or generate profiles
        self._profiles = self._load_profiles()
    
    def _load_profiles(self) -> Dict[str, FingerprintProfile]:
        """Load saved fingerprint profiles."""
        profiles = {}
        profile_file = self.profile_dir / "fingerprints.json"
        
        if profile_file.exists():
            try:
                with open(profile_file, 'r') as f:
                    data = json.load(f)
                    for profile_id, profile_data in data.items():
                        profiles[profile_id] = FingerprintProfile(**profile_data)
            except Exception:
                pass
        
        return profiles
    
    def _save_profiles(self) -> None:
        """Save fingerprint profiles to disk."""
        profile_file = self.profile_dir / "fingerprints.json"
        
        data = {}
        for profile_id, profile in self._profiles.items():
            data[profile_id] = {
                "user_agent": profile.user_agent,
                "platform": profile.platform,
                "languages": profile.languages,
                "screen_width": profile.screen_width,
                "screen_height": profile.screen_height,
                "color_depth": profile.color_depth,
                "pixel_ratio": profile.pixel_ratio,
                "timezone": profile.timezone,
                "webgl_vendor": profile.webgl_vendor,
                "webgl_renderer": profile.webgl_renderer,
                "canvas_hash": profile.canvas_hash,
                "audio_hash": profile.audio_hash,
                "fonts": profile.fonts,
                "plugins": profile.plugins,
                "hardware_concurrency": profile.hardware_concurrency,
                "device_memory": profile.device_memory,
                "touch_support": profile.touch_support,
                "do_not_track": profile.do_not_track,
                "max_touch_points": profile.max_touch_points,
                "session_id": profile.session_id,
            }
        
        with open(profile_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    async def apply_to_context(self, context: BrowserContext) -> None:
        """Apply stealth settings to a browser context."""
        # Generate or select fingerprint profile
        if not self._current_profile:
            profile_id = f"profile_{len(self._profiles)}"
            self._current_profile = FingerprintGenerator.generate_profile()
            self._profiles[profile_id] = self._current_profile
            self._save_profiles()
        
        profile = self._current_profile
        
        # Set user agent
        await context.set_user_agent(profile.user_agent)
        
        # Set viewport
        await context.set_viewport_size({
            "width": profile.screen_width,
            "height": profile.screen_height,
        })
        
        # Add stealth scripts
        await self._inject_stealth_scripts(context)
    
    async def _inject_stealth_scripts(self, context: BrowserContext) -> None:
        """Inject JavaScript to override fingerprint properties."""
        profile = self._current_profile
        
        stealth_script = """
        // Override navigator properties
        Object.defineProperty(navigator, 'languages', {
            get: () => %s,
        });
        
        Object.defineProperty(navigator, 'platform', {
            get: () => '%s',
        });
        
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => %d,
        });
        
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => %d,
        });
        
        Object.defineProperty(navigator, 'maxTouchPoints', {
            get: () => %d,
        });
        
        // Override screen properties
        Object.defineProperty(screen, 'width', {
            get: () => %d,
        });
        
        Object.defineProperty(screen, 'height', {
            get: () => %d,
        });
        
        Object.defineProperty(screen, 'colorDepth', {
            get: () => %d,
        });
        
        Object.defineProperty(screen, 'pixelDepth', {
            get: () => %d,
        });
        
        // Override WebGL
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) {
                return '%s';
            }
            if (parameter === 37446) {
                return '%s';
            }
            return getParameter.call(this, parameter);
        };
        
        // Override canvas fingerprint
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png' && this.width === 16 && this.height === 16) {
                // This is likely a fingerprinting attempt
                return 'data:image/png;base64,%s';
            }
            return toDataURL.apply(this, arguments);
        };
        
        // Override audio fingerprint
        const createOscillator = AudioContext.prototype.createOscillator;
        AudioContext.prototype.createOscillator = function() {
            const oscillator = createOscillator.call(this);
            const originalConnect = oscillator.connect;
            oscillator.connect = function(destination) {
                if (destination instanceof AnalyserNode) {
                    // Likely fingerprinting, add noise
                    const noise = Math.random() * 0.0001;
                    const originalGetFloatFrequencyData = destination.getFloatFrequencyData;
                    destination.getFloatFrequencyData = function(array) {
                        originalGetFloatFrequencyData.call(this, array);
                        for (let i = 0; i < array.length; i++) {
                            array[i] += noise;
                        }
                    };
                }
                return originalConnect.call(this, destination);
            };
            return oscillator;
        };
        
        // Override plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = %s;
                const pluginArray = [];
                plugins.forEach((p, i) => {
                    pluginArray[i] = {
                        name: p.name,
                        filename: p.filename,
                        description: p.name,
                        length: 1,
                    };
                });
                pluginArray.length = plugins.length;
                return pluginArray;
            },
        });
        
        // Override fonts (partial, via CSS)
        const fontFaceSet = document.fonts;
        const originalCheck = fontFaceSet.check;
        fontFaceSet.check = function(font, text) {
            const availableFonts = %s;
            const requestedFont = font.split(' ').pop().replace(/"/g, '');
            if (availableFonts.includes(requestedFont)) {
                return true;
            }
            return originalCheck.call(this, font, text);
        };
        
        // Hide automation indicators
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
        
        // Override permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Chrome-specific overrides
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {},
        };
        
        // Override connection
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                rtt: 50,
                downlink: 10,
                effectiveType: '4g',
                saveData: false,
            }),
        });
        """ % (
            json.dumps(profile.languages),
            profile.platform,
            profile.hardware_concurrency,
            profile.device_memory,
            profile.max_touch_points,
            profile.screen_width,
            profile.screen_height,
            profile.color_depth,
            profile.color_depth,
            profile.webgl_vendor,
            profile.webgl_renderer,
            profile.canvas_hash,
            json.dumps(profile.plugins),
            json.dumps(profile.fonts),
        )
        
        await context.add_init_script(stealth_script)
    
    async def apply_to_page(self, page: Page) -> None:
        """Apply stealth enhancements to a page."""
        # Create enhanced mouse and keyboard
        self._mouse = StealthMouse(page, self.level)
        self._keyboard = StealthKeyboard(page, self.level)
        
        # Add additional page-level stealth
        if self.level in [StealthLevel.AGGRESSIVE, StealthLevel.HUMAN]:
            await self._apply_page_stealth(page)
    
    async def _apply_page_stealth(self, page: Page) -> None:
        """Apply additional stealth measures to page."""
        # Override common detection methods
        await page.evaluate("""() => {
            // Override iframe contentWindow
            const originalContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                get: function() {
                    const win = originalContentWindow.get.call(this);
                    if (win) {
                        Object.defineProperty(win, 'navigator', {
                            get: () => navigator,
                        });
                    }
                    return win;
                },
            });
            
            // Override toString methods
            const nativeToStringFunctionString = Error.toString().replace(/Error/g, 'toString');
            const origProto = Function.prototype.toString;
            const myProto = function() {
                if (this === myProto) {
                    return nativeToStringFunctionString;
                }
                return origProto.call(this);
            };
            Function.prototype.toString = myProto;
            
            // Hide CDP detection
            window.__playwright = undefined;
            window.__pw_manual = undefined;
            
            // Override Date to prevent timing attacks
            const originalDate = Date;
            let timeShift = 0;
            
            class StealthDate extends originalDate {
                constructor(...args) {
                    if (args.length === 0) {
                        super(originalDate.now() + timeShift);
                    } else {
                        super(...args);
                    }
                }
                
                static now() {
                    return originalDate.now() + timeShift;
                }
            }
            
            window.Date = StealthDate;
        }""")
    
    async def rotate_proxy(self, context: BrowserContext) -> Optional[ProxyConfig]:
        """Rotate to next proxy in pool."""
        if not self.proxy_pool:
            return None
        
        # Select proxy based on success rate and last used time
        available_proxies = [
            p for p in self.proxy_pool 
            if time.time() - p.last_used > 60  # Don't reuse within 60 seconds
        ]
        
        if not available_proxies:
            available_proxies = self.proxy_pool
        
        # Weight by success rate
        weights = [p.success_rate for p in available_proxies]
        selected = random.choices(available_proxies, weights=weights, k=1)[0]
        
        # Update proxy usage
        selected.last_used = time.time()
        self._current_proxy = selected
        
        # Note: Actual proxy rotation requires creating a new context
        # This method just selects the next proxy
        return selected
    
    async def create_stealth_page(self, browser: Browser) -> Tuple[BrowserPage, BrowserContext]:
        """Create a new page with full stealth configuration."""
        # Select proxy if available
        proxy_config = None
        if self.proxy_pool:
            proxy_config = await self.rotate_proxy(None)  # Context not created yet
        
        # Create context with proxy
        context_options = {}
        if proxy_config:
            context_options["proxy"] = {
                "server": proxy_config.server,
            }
            if proxy_config.username:
                context_options["proxy"]["username"] = proxy_config.username
            if proxy_config.password:
                context_options["proxy"]["password"] = proxy_config.password
        
        context = await browser.new_context(**context_options)
        
        # Apply stealth to context
        await self.apply_to_context(context)
        
        # Create page
        page = await context.new_page()
        
        # Apply page-level stealth
        await self.apply_to_page(page)
        
        # Wrap in our enhanced Page class
        stealth_page = BrowserPage(page)
        
        return stealth_page, context
    
    def get_mouse(self) -> Optional[StealthMouse]:
        """Get the stealth-enhanced mouse."""
        return self._mouse
    
    def get_keyboard(self) -> Optional[StealthKeyboard]:
        """Get the stealth-enhanced keyboard."""
        return self._keyboard
    
    async def simulate_human_interaction(self, page: Page) -> None:
        """Simulate random human-like interactions on page."""
        if self.level == StealthLevel.HUMAN:
            # Random mouse movements
            for _ in range(random.randint(2, 5)):
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                await self._mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.5, 2.0))
            
            # Random scrolling
            scroll_amount = random.randint(-300, 300)
            await page.mouse.wheel(0, scroll_amount)
            await asyncio.sleep(random.uniform(0.3, 1.5))


class ProxyRotator:
    """Manages proxy rotation with health checking."""
    
    def __init__(self, proxy_list: List[Dict[str, Any]]):
        self.proxies = []
        for proxy_data in proxy_list:
            self.proxies.append(ProxyConfig(**proxy_data))
        
        self._current_index = 0
    
    async def get_next_proxy(self) -> ProxyConfig:
        """Get next proxy with rotation logic."""
        if not self.proxies:
            raise ValueError("No proxies available")
        
        # Simple round-robin for now
        proxy = self.proxies[self._current_index]
        self._current_index = (self._current_index + 1) % len(self.proxies)
        
        return proxy
    
    async def mark_proxy_success(self, proxy: ProxyConfig) -> None:
        """Mark proxy as successful."""
        proxy.success_rate = min(1.0, proxy.success_rate + 0.1)
    
    async def mark_proxy_failure(self, proxy: ProxyConfig) -> None:
        """Mark proxy as failed."""
        proxy.success_rate = max(0.1, proxy.success_rate - 0.3)


# Convenience function for quick stealth setup
async def setup_stealth(page: Page, 
                        level: StealthLevel = StealthLevel.STANDARD) -> StealthMode:
    """Quick setup for stealth mode on an existing page."""
    stealth = StealthMode(level=level)
    await stealth.apply_to_page(page)
    return stealth


# Integration with existing Page class
def enhance_page_with_stealth(page: BrowserPage, 
                             level: StealthLevel = StealthLevel.STANDARD) -> BrowserPage:
    """Enhance an existing Page instance with stealth capabilities."""
    stealth = StealthMode(level=level)
    
    # Override mouse and keyboard
    original_mouse = page.mouse
    original_keyboard = page.page.keyboard
    
    stealth_mouse = StealthMouse(page.page, level)
    stealth_keyboard = StealthKeyboard(page.page, level)
    
    page.mouse = stealth_mouse
    # Note: We can't easily replace the keyboard in the existing Page class
    # Users should use stealth_keyboard directly
    
    return page


# Export main classes
__all__ = [
    "StealthMode",
    "StealthLevel",
    "FingerprintProfile",
    "FingerprintGenerator",
    "StealthMouse",
    "StealthKeyboard",
    "HumanBehavior",
    "ProxyConfig",
    "ProxyRotator",
    "setup_stealth",
    "enhance_page_with_stealth",
]