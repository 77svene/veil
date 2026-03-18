"""Page class for page-level operations."""

from typing import TYPE_CHECKING, TypeVar, List, Dict, Any, Optional, Tuple
import asyncio
import json
import base64
import hashlib
import time
import random
import math
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel
import statistics
from collections import deque
import numpy as np

from veil import logger
from veil.actor.utils import get_key_info
from veil.dom.serializer.serializer import DOMTreeSerializer
from veil.dom.service import DomService
from veil.llm.messages import SystemMessage, UserMessage

T = TypeVar('T', bound=BaseModel)

if TYPE_CHECKING:
    from cdp_use.cdp.dom.commands import (
        DescribeNodeParameters,
        QuerySelectorAllParameters,
    )
    from cdp_use.cdp.emulation.commands import SetDeviceMetricsOverrideParameters
    from cdp_use.cdp.input.commands import (
        DispatchKeyEventParameters,
    )
    from cdp_use.cdp.page.commands import CaptureScreenshotParameters, NavigateParameters, NavigateToHistoryEntryParameters
    from cdp_use.cdp.runtime.commands import EvaluateParameters
    from cdp_use.cdp.target.commands import (
        AttachToTargetParameters,
        GetTargetInfoParameters,
    )
    from cdp_use.cdp.target.types import TargetInfo

    from veil.browser.session import BrowserSession
    from veil.llm.base import BaseChatModel

    from .element import Element
    from .mouse import Mouse


class InteractionState(Enum):
    """State machine for automation interactions."""
    INITIAL = "initial"
    ATTEMPTING = "attempting"
    FALLBACK_1 = "fallback_1"
    FALLBACK_2 = "fallback_2"
    FALLBACK_3 = "fallback_3"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class AutomationPattern:
    """Tracks success/failure patterns for automation strategies."""
    domain: str
    url_pattern: str
    selector_type: str
    selector_value: str
    interaction_type: str
    success_count: int = 0
    failure_count: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    fallback_strategies: List[Dict[str, Any]] = field(default_factory=list)
    success_rate: float = 0.0
    
    def update_success(self):
        """Update pattern after successful interaction."""
        self.success_count += 1
        self.last_success = time.time()
        self._update_success_rate()
    
    def update_failure(self):
        """Update pattern after failed interaction."""
        self.failure_count += 1
        self.last_failure = time.time()
        self._update_success_rate()
    
    def _update_success_rate(self):
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        self.success_rate = self.success_count / total if total > 0 else 0.0


@dataclass
class PerformanceMetrics:
    """Tracks real-time performance metrics."""
    fcp: float = 0.0  # First Contentful Paint
    lcp: float = 0.0  # Largest Contentful Paint
    tti: float = 0.0  # Time to Interactive
    network_latency: float = 0.0
    dom_content_loaded: float = 0.0
    load_event_end: float = 0.0
    page_load_time: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class StealthConfig:
    """Configuration for stealth mode and anti-detection."""
    enabled: bool = True
    fingerprint_randomization: bool = True
    human_mouse_movement: bool = True
    variable_typing_speed: bool = True
    realistic_scrolling: bool = True
    proxy_rotation: bool = False
    browser_profile: str = "default"
    mouse_movement_speed: float = 1.0
    typing_speed_variation: float = 0.3
    scroll_behavior: str = "smooth"
    min_human_delay: float = 0.05
    max_human_delay: float = 0.3
    mouse_curve_intensity: float = 0.5
    fingerprint_seed: Optional[int] = None


@dataclass
class DebugStep:
    """Represents a single step in the visual debugging timeline."""
    step_id: int
    timestamp: float
    action_type: str
    action_params: Dict[str, Any]
    dom_snapshot: str
    screenshot: Optional[str] = None
    network_requests: List[Dict[str, Any]] = field(default_factory=list)
    network_responses: List[Dict[str, Any]] = field(default_factory=list)
    console_logs: List[Dict[str, Any]] = field(default_factory=list)
    performance_metrics: Optional[Dict[str, float]] = None
    element_highlight: Optional[Dict[str, Any]] = None
    success: bool = True
    error_message: Optional[str] = None
    execution_time: float = 0.0
    multimodal_analysis: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert step to dictionary for serialization."""
        return {
            "step_id": self.step_id,
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "action_params": self.action_params,
            "dom_snapshot": self.dom_snapshot,
            "screenshot": self.screenshot,
            "network_requests": self.network_requests,
            "network_responses": self.network_responses,
            "console_logs": self.console_logs,
            "performance_metrics": self.performance_metrics,
            "element_highlight": self.element_highlight,
            "success": self.success,
            "error_message": self.error_message,
            "execution_time": self.execution_time,
            "multimodal_analysis": self.multimodal_analysis
        }


@dataclass
class MultimodalElement:
    """Represents a multimodal element with visual and semantic information."""
    element_type: str  # "image", "canvas", "pdf", "chart", "video", "iframe"
    bbox: Dict[str, float]  # x, y, width, height
    confidence: float = 1.0
    text_content: Optional[str] = None
    semantic_label: Optional[str] = None
    data_content: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultimodalPageAnalysis:
    """Complete multimodal analysis of a page."""
    timestamp: float = field(default_factory=time.time)
    dom_analysis: Dict[str, Any] = field(default_factory=dict)
    visual_elements: List[MultimodalElement] = field(default_factory=list)
    text_from_images: List[Dict[str, Any]] = field(default_factory=list)
    chart_data: List[Dict[str, Any]] = field(default_factory=list)
    pdf_content: List[Dict[str, Any]] = field(default_factory=list)
    canvas_analysis: List[Dict[str, Any]] = field(default_factory=list)
    media_analysis: List[Dict[str, Any]] = field(default_factory=list)
    semantic_understanding: Dict[str, Any] = field(default_factory=dict)
    accessibility_tree: Optional[Dict[str, Any]] = None
    page_summary: str = ""
    key_information: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert analysis to dictionary."""
        return {
            "timestamp": self.timestamp,
            "dom_analysis": self.dom_analysis,
            "visual_elements": [vars(elem) for elem in self.visual_elements],
            "text_from_images": self.text_from_images,
            "chart_data": self.chart_data,
            "pdf_content": self.pdf_content,
            "canvas_analysis": self.canvas_analysis,
            "media_analysis": self.media_analysis,
            "semantic_understanding": self.semantic_understanding,
            "accessibility_tree": self.accessibility_tree,
            "page_summary": self.page_summary,
            "key_information": self.key_information
        }


class MultiModalUnderstandingEngine:
    """Engine for multimodal page understanding combining DOM, visual, and semantic analysis."""
    
    def __init__(self, page: 'Page'):
        self.page = page
        self.dom_serializer = DOMTreeSerializer()
        self.ocr_cache = {}
        self.chart_recognition_cache = {}
        self.pdf_cache = {}
        
    async def analyze_page(self, include_screenshot: bool = True) -> MultimodalPageAnalysis:
        """Perform comprehensive multimodal analysis of the current page."""
        analysis = MultimodalPageAnalysis()
        
        # Parallel analysis tasks
        tasks = [
            self._analyze_dom(),
            self._analyze_visual_content(),
            self._extract_text_from_images(),
            self._recognize_charts(),
            self._parse_pdfs(),
            self._analyze_canvas_elements(),
            self._analyze_embedded_media(),
            self._build_semantic_understanding()
        ]
        
        if include_screenshot:
            tasks.append(self._capture_and_analyze_screenshot())
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Analysis task {i} failed: {result}")
                continue
                
            if i == 0:  # DOM analysis
                analysis.dom_analysis = result
            elif i == 1:  # Visual content
                analysis.visual_elements = result
            elif i == 2:  # Text from images
                analysis.text_from_images = result
            elif i == 3:  # Charts
                analysis.chart_data = result
            elif i == 4:  # PDFs
                analysis.pdf_content = result
            elif i == 5:  # Canvas
                analysis.canvas_analysis = result
            elif i == 6:  # Media
                analysis.media_analysis = result
            elif i == 7:  # Semantic
                analysis.semantic_understanding = result
        
        # Generate page summary and extract key information
        analysis.page_summary = await self._generate_page_summary(analysis)
        analysis.key_information = await self._extract_key_information(analysis)
        
        return analysis
    
    async def _analyze_dom(self) -> Dict[str, Any]:
        """Analyze DOM structure and content."""
        try:
            # Get DOM tree
            dom_tree = await self.page.get_dom_tree()
            
            # Extract important DOM elements
            important_elements = await self._extract_important_dom_elements(dom_tree)
            
            # Analyze DOM structure
            structure_analysis = await self._analyze_dom_structure(dom_tree)
            
            return {
                "tree": dom_tree,
                "important_elements": important_elements,
                "structure": structure_analysis,
                "element_count": len(dom_tree.get("children", [])),
                "interactive_elements": await self._find_interactive_elements(dom_tree)
            }
        except Exception as e:
            logger.error(f"DOM analysis failed: {e}")
            return {"error": str(e)}
    
    async def _analyze_visual_content(self) -> List[MultimodalElement]:
        """Analyze visual content on the page."""
        visual_elements = []
        
        try:
            # Get all visual elements using JavaScript
            visual_elements_js = await self.page.evaluate("""
                () => {
                    const elements = [];
                    
                    // Images
                    document.querySelectorAll('img').forEach(img => {
                        if (img.offsetWidth > 0 && img.offsetHeight > 0) {
                            elements.push({
                                type: 'image',
                                bbox: img.getBoundingClientRect(),
                                src: img.src,
                                alt: img.alt || '',
                                width: img.naturalWidth,
                                height: img.naturalHeight
                            });
                        }
                    });
                    
                    // Canvas elements
                    document.querySelectorAll('canvas').forEach(canvas => {
                        if (canvas.offsetWidth > 0 && canvas.offsetHeight > 0) {
                            elements.push({
                                type: 'canvas',
                                bbox: canvas.getBoundingClientRect(),
                                width: canvas.width,
                                height: canvas.height,
                                id: canvas.id,
                                className: canvas.className
                            });
                        }
                    });
                    
                    // Video elements
                    document.querySelectorAll('video').forEach(video => {
                        if (video.offsetWidth > 0 && video.offsetHeight > 0) {
                            elements.push({
                                type: 'video',
                                bbox: video.getBoundingClientRect(),
                                src: video.src,
                                poster: video.poster,
                                duration: video.duration,
                                currentTime: video.currentTime
                            });
                        }
                    });
                    
                    // SVG elements
                    document.querySelectorAll('svg').forEach(svg => {
                        if (svg.offsetWidth > 0 && svg.offsetHeight > 0) {
                            elements.push({
                                type: 'svg',
                                bbox: svg.getBoundingClientRect(),
                                width: svg.getAttribute('width'),
                                height: svg.getAttribute('height'),
                                viewBox: svg.getAttribute('viewBox')
                            });
                        }
                    });
                    
                    // iframes
                    document.querySelectorAll('iframe').forEach(iframe => {
                        if (iframe.offsetWidth > 0 && iframe.offsetHeight > 0) {
                            elements.push({
                                type: 'iframe',
                                bbox: iframe.getBoundingClientRect(),
                                src: iframe.src,
                                title: iframe.title
                            });
                        }
                    });
                    
                    return elements;
                }
            """)
            
            for elem_data in visual_elements_js:
                bbox = elem_data.get("bbox", {})
                element = MultimodalElement(
                    element_type=elem_data["type"],
                    bbox={
                        "x": bbox.get("x", 0),
                        "y": bbox.get("y", 0),
                        "width": bbox.get("width", 0),
                        "height": bbox.get("height", 0)
                    },
                    source_url=elem_data.get("src"),
                    metadata=elem_data
                )
                visual_elements.append(element)
                
        except Exception as e:
            logger.error(f"Visual content analysis failed: {e}")
        
        return visual_elements
    
    async def _extract_text_from_images(self) -> List[Dict[str, Any]]:
        """Extract text from images using OCR."""
        text_results = []
        
        try:
            # Get all images on the page
            images = await self.page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img'))
                        .filter(img => img.offsetWidth > 0 && img.offsetHeight > 0)
                        .map(img => ({
                            src: img.src,
                            alt: img.alt,
                            bbox: img.getBoundingClientRect(),
                            width: img.naturalWidth,
                            height: img.naturalHeight
                        }));
                }
            """)
            
            for img in images:
                img_hash = hashlib.md5(img["src"].encode()).hexdigest()
                
                # Check cache
                if img_hash in self.ocr_cache:
                    text_results.append(self.ocr_cache[img_hash])
                    continue
                
                try:
                    # Capture image data
                    image_data = await self._capture_element_image(img["bbox"])
                    
                    # Perform OCR (simplified - in production would use Tesseract or cloud OCR)
                    ocr_result = await self._perform_ocr(image_data)
                    
                    result = {
                        "image_src": img["src"],
                        "alt_text": img.get("alt", ""),
                        "bbox": img["bbox"],
                        "extracted_text": ocr_result["text"],
                        "confidence": ocr_result["confidence"],
                        "language": ocr_result.get("language", "en")
                    }
                    
                    self.ocr_cache[img_hash] = result
                    text_results.append(result)
                    
                except Exception as e:
                    logger.warning(f"OCR failed for image {img['src']}: {e}")
                    
        except Exception as e:
            logger.error(f"Text extraction from images failed: {e}")
        
        return text_results
    
    async def _recognize_charts(self) -> List[Dict[str, Any]]:
        """Recognize and extract data from charts and graphs."""
        chart_results = []
        
        try:
            # Identify potential chart elements
            chart_candidates = await self.page.evaluate("""
                () => {
                    const candidates = [];
                    
                    // Look for common chart libraries
                    const chartSelectors = [
                        'canvas[id*="chart"]',
                        'svg[class*="chart"]',
                        'div[class*="chart"]',
                        'div[class*="graph"]',
                        'div[class*="plot"]',
                        '[data-chart-type]',
                        '.recharts-wrapper',
                        '.chartjs-container',
                        '.highcharts-container'
                    ];
                    
                    chartSelectors.forEach(selector => {
                        document.querySelectorAll(selector).forEach(el => {
                            if (el.offsetWidth > 100 && el.offsetHeight > 100) {
                                candidates.push({
                                    selector: selector,
                                    element: el.tagName.toLowerCase(),
                                    bbox: el.getBoundingClientRect(),
                                    id: el.id,
                                    className: el.className,
                                    innerHTML: el.innerHTML.substring(0, 500)
                                });
                            }
                        });
                    });
                    
                    return candidates;
                }
            """)
            
            for candidate in chart_candidates:
                chart_hash = hashlib.md5(str(candidate).encode()).hexdigest()
                
                # Check cache
                if chart_hash in self.chart_recognition_cache:
                    chart_results.append(self.chart_recognition_cache[chart_hash])
                    continue
                
                try:
                    # Analyze chart
                    chart_analysis = await self._analyze_chart_element(candidate)
                    
                    if chart_analysis:
                        self.chart_recognition_cache[chart_hash] = chart_analysis
                        chart_results.append(chart_analysis)
                        
                except Exception as e:
                    logger.warning(f"Chart recognition failed: {e}")
                    
        except Exception as e:
            logger.error(f"Chart recognition failed: {e}")
        
        return chart_results
    
    async def _parse_pdfs(self) -> List[Dict[str, Any]]:
        """Parse PDF content embedded in the page."""
        pdf_results = []
        
        try:
            # Find PDF embeds
            pdf_elements = await self.page.evaluate("""
                () => {
                    const pdfs = [];
                    
                    // Object/embed tags
                    document.querySelectorAll('object[type="application/pdf"], embed[type="application/pdf"]').forEach(el => {
                        pdfs.push({
                            type: 'embed',
                            data: el.data || el.getAttribute('data'),
                            bbox: el.getBoundingClientRect(),
                            width: el.width,
                            height: el.height
                        });
                    });
                    
                    // iframes with PDF
                    document.querySelectorAll('iframe').forEach(iframe => {
                        if (iframe.src && iframe.src.includes('.pdf')) {
                            pdfs.push({
                                type: 'iframe',
                                src: iframe.src,
                                bbox: iframe.getBoundingClientRect()
                            });
                        }
                    });
                    
                    // Links to PDFs
                    document.querySelectorAll('a[href$=".pdf"]').forEach(a => {
                        pdfs.push({
                            type: 'link',
                            href: a.href,
                            text: a.textContent,
                            bbox: a.getBoundingClientRect()
                        });
                    });
                    
                    return pdfs;
                }
            """)
            
            for pdf_elem in pdf_elements:
                pdf_hash = hashlib.md5(str(pdf_elem).encode()).hexdigest()
                
                # Check cache
                if pdf_hash in self.pdf_cache:
                    pdf_results.append(self.pdf_cache[pdf_hash])
                    continue
                
                try:
                    # Parse PDF
                    pdf_content = await self._extract_pdf_content(pdf_elem)
                    
                    if pdf_content:
                        self.pdf_cache[pdf_hash] = pdf_content
                        pdf_results.append(pdf_content)
                        
                except Exception as e:
                    logger.warning(f"PDF parsing failed: {e}")
                    
        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
        
        return pdf_results
    
    async def _analyze_canvas_elements(self) -> List[Dict[str, Any]]:
        """Analyze canvas elements and extract their content."""
        canvas_results = []
        
        try:
            canvas_elements = await self.page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('canvas'))
                        .filter(canvas => canvas.offsetWidth > 0 && canvas.offsetHeight > 0)
                        .map(canvas => ({
                            id: canvas.id,
                            className: canvas.className,
                            width: canvas.width,
                            height: canvas.height,
                            bbox: canvas.getBoundingClientRect(),
                            // Try to get canvas context
                            has2d: !!canvas.getContext('2d'),
                            hasWebgl: !!canvas.getContext('webgl') || !!canvas.getContext('experimental-webgl')
                        }));
                }
            """)
            
            for canvas in canvas_elements:
                try:
                    # Extract canvas content
                    canvas_analysis = await self._extract_canvas_content(canvas)
                    canvas_results.append(canvas_analysis)
                    
                except Exception as e:
                    logger.warning(f"Canvas analysis failed: {e}")
                    
        except Exception as e:
            logger.error(f"Canvas analysis failed: {e}")
        
        return canvas_results
    
    async def _analyze_embedded_media(self) -> List[Dict[str, Any]]:
        """Analyze embedded media elements."""
        media_results = []
        
        try:
            media_elements = await self.page.evaluate("""
                () => {
                    const media = [];
                    
                    // Video elements
                    document.querySelectorAll('video').forEach(video => {
                        media.push({
                            type: 'video',
                            src: video.src,
                            currentSrc: video.currentSrc,
                            poster: video.poster,
                            duration: video.duration,
                            currentTime: video.currentTime,
                            paused: video.paused,
                            muted: video.muted,
                            volume: video.volume,
                            bbox: video.getBoundingClientRect()
                        });
                    });
                    
                    // Audio elements
                    document.querySelectorAll('audio').forEach(audio => {
                        media.push({
                            type: 'audio',
                            src: audio.src,
                            currentSrc: audio.currentSrc,
                            duration: audio.duration,
                            currentTime: audio.currentTime,
                            paused: audio.paused,
                            muted: audio.muted,
                            volume: audio.volume,
                            bbox: audio.getBoundingClientRect()
                        });
                    });
                    
                    // Embedded media (YouTube, Vimeo, etc.)
                    document.querySelectorAll('iframe[src*="youtube"], iframe[src*="vimeo"], iframe[src*="dailymotion"]').forEach(iframe => {
                        media.push({
                            type: 'embedded_video',
                            src: iframe.src,
                            title: iframe.title,
                            bbox: iframe.getBoundingClientRect()
                        });
                    });
                    
                    return media;
                }
            """)
            
            for media_elem in media_elements:
                media_results.append(media_elem)
                    
        except Exception as e:
            logger.error(f"Media analysis failed: {e}")
        
        return media_results
    
    async def _build_semantic_understanding(self) -> Dict[str, Any]:
        """Build semantic understanding of the page content."""
        try:
            # Extract main content
            main_content = await self.page.evaluate("""
                () => {
                    const main = document.querySelector('main') || 
                                 document.querySelector('[role="main"]') ||
                                 document.querySelector('article') ||
                                 document.body;
                    
                    return {
                        text: main.innerText,
                        html: main.innerHTML.substring(0, 10000),
                        headings: Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                            .map(h => ({
                                level: parseInt(h.tagName[1]),
                                text: h.textContent.trim()
                            })),
                        paragraphs: Array.from(document.querySelectorAll('p'))
                            .map(p => p.textContent.trim())
                            .filter(text => text.length > 20)
                    };
                }
            """)
            
            # Analyze page structure
            page_structure = await self._analyze_page_structure()
            
            # Extract key entities and topics
            entities = await self._extract_entities(main_content["text"])
            
            return {
                "main_content": main_content,
                "page_structure": page_structure,
                "entities": entities,
                "topic_classification": await self._classify_topic(main_content["text"]),
                "sentiment_analysis": await self._analyze_sentiment(main_content["text"])
            }
            
        except Exception as e:
            logger.error(f"Semantic understanding failed: {e}")
            return {"error": str(e)}
    
    async def _capture_and_analyze_screenshot(self) -> Dict[str, Any]:
        """Capture and analyze screenshot for visual understanding."""
        try:
            # Capture screenshot
            screenshot = await self.page.screenshot()
            
            # Analyze screenshot (simplified - would use computer vision in production)
            analysis = {
                "screenshot_captured": True,
                "timestamp": time.time(),
                "dimensions": {
                    "width": await self.page.evaluate("window.innerWidth"),
                    "height": await self.page.evaluate("window.innerHeight")
                },
                "color_analysis": await self._analyze_screenshot_colors(screenshot),
                "layout_analysis": await self._analyze_layout(screenshot)
            }
            
            return analysis
            
        except Exception as e:
            logger.error(f"Screenshot analysis failed: {e}")
            return {"error": str(e)}
    
    async def _generate_page_summary(self, analysis: MultimodalPageAnalysis) -> str:
        """Generate a summary of the page based on multimodal analysis."""
        try:
            summary_parts = []
            
            # Add DOM-based summary
            if analysis.dom_analysis.get("important_elements"):
                summary_parts.append(f"Page contains {len(analysis.dom_analysis['important_elements'])} important elements.")
            
            # Add visual content summary
            if analysis.visual_elements:
                element_types = {}
                for elem in analysis.visual_elements:
                    element_types[elem.element_type] = element_types.get(elem.element_type, 0) + 1
                summary_parts.append(f"Visual elements: {', '.join([f'{count} {typ}' for typ, count in element_types.items()])}")
            
            # Add text from images
            if analysis.text_from_images:
                total_text = sum(len(item.get("extracted_text", "")) for item in analysis.text_from_images)
                summary_parts.append(f"Extracted {total_text} characters of text from {len(analysis.text_from_images)} images.")
            
            # Add chart information
            if analysis.chart_data:
                summary_parts.append(f"Found {len(analysis.chart_data)} charts/graphs on the page.")
            
            # Add semantic understanding
            if analysis.semantic_understanding.get("topic_classification"):
                topic = analysis.semantic_understanding["topic_classification"]
                summary_parts.append(f"Page topic: {topic}")
            
            return " ".join(summary_parts) if summary_parts else "Page analysis completed."
            
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return "Unable to generate page summary."
    
    async def _extract_key_information(self, analysis: MultimodalPageAnalysis) -> List[str]:
        """Extract key information from the page."""
        key_info = []
        
        try:
            # Extract from semantic understanding
            if analysis.semantic_understanding.get("entities"):
                entities = analysis.semantic_understanding["entities"]
                key_info.extend([f"Entity: {entity}" for entity in entities[:5]])
            
            # Extract from charts
            for chart in analysis.chart_data[:3]:
                if chart.get("title"):
                    key_info.append(f"Chart: {chart['title']}")
            
            # Extract from PDFs
            for pdf in analysis.pdf_content[:2]:
                if pdf.get("title"):
                    key_info.append(f"PDF: {pdf['title']}")
            
            # Extract from main content headings
            if analysis.semantic_understanding.get("main_content", {}).get("headings"):
                headings = analysis.semantic_understanding["main_content"]["headings"]
                key_info.extend([f"Heading: {h['text']}" for h in headings[:5]])
            
            return key_info[:10]  # Limit to top 10
            
        except Exception as e:
            logger.error(f"Key information extraction failed: {e}")
            return []
    
    # Helper methods (simplified implementations)
    async def _extract_important_dom_elements(self, dom_tree: Dict) -> List[Dict]:
        """Extract important DOM elements."""
        return []
    
    async def _analyze_dom_structure(self, dom_tree: Dict) -> Dict:
        """Analyze DOM structure."""
        return {}
    
    async def _find_interactive_elements(self, dom_tree: Dict) -> List[Dict]:
        """Find interactive elements in DOM."""
        return []
    
    async def _capture_element_image(self, bbox: Dict) -> str:
        """Capture image of an element."""
        return ""
    
    async def _perform_ocr(self, image_data: str) -> Dict:
        """Perform OCR on image data."""
        return {"text": "", "confidence": 0.0}
    
    async def _analyze_chart_element(self, candidate: Dict) -> Optional[Dict]:
        """Analyze a chart element."""
        return None
    
    async def _extract_pdf_content(self, pdf_elem: Dict) -> Optional[Dict]:
        """Extract content from PDF."""
        return None
    
    async def _extract_canvas_content(self, canvas: Dict) -> Dict:
        """Extract content from canvas."""
        return {}
    
    async def _analyze_page_structure(self) -> Dict:
        """Analyze page structure."""
        return {}
    
    async def _extract_entities(self, text: str) -> List[str]:
        """Extract entities from text."""
        return []
    
    async def _classify_topic(self, text: str) -> str:
        """Classify page topic."""
        return "unknown"
    
    async def _analyze_sentiment(self, text: str) -> Dict:
        """Analyze sentiment of text."""
        return {}
    
    async def _analyze_screenshot_colors(self, screenshot: str) -> Dict:
        """Analyze colors in screenshot."""
        return {}
    
    async def _analyze_layout(self, screenshot: str) -> Dict:
        """Analyze layout from screenshot."""
        return {}


class VisualDebugger:
    """Visual debugging and replay system for automation sessions."""
    
    def __init__(self, page: 'Page'):
        self.page = page
        self.steps: List[DebugStep] = []
        self.current_step_id = 0
        self.is_recording = False
        self.recording_start_time = 0.0
        self.network_requests: List[Dict[str, Any]] = []
        self.network_responses: List[Dict[str, Any]] = []
        self.console_logs: List[Dict[str, Any]] = []
        self.dom_serializer = DOMTreeSerializer()
        self.multimodal_engine = MultiModalUnderstandingEngine(page)
        
    def start_recording(self):
        """Start recording automation session."""
        self.is_recording = True
        self.recording_start_time = time.time()
        self.steps = []
        self.current_step_id = 0
        self.network_requests = []
        self.network_responses = []
        self.console_logs = []
        logger.info("Visual debugging recording started")
        
    def stop_recording(self):
        """Stop recording automation session."""
        self.is_recording = False
        logger.info(f"Visual debugging recording stopped. Captured {len(self.steps)} steps")
        
    async def record_step(self, action_type: str, action_params: Dict[str, Any], 
                         element_highlight: Optional[Dict[str, Any]] = None,
                         success: bool = True, error_message: Optional[str] = None,
                         execution_time: float = 0.0,
                         include_multimodal: bool = False):
        """Record a single automation step with visual data."""
        if not self.is_recording:
            return
            
        # Capture DOM snapshot
        dom_snapshot = await self._capture_dom_snapshot()
        
        # Capture screenshot (async, will be added later)
        screenshot = None
        try:
            screenshot = await self._capture_screenshot()
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
        
        # Capture performance metrics
        performance_metrics = await self._capture_performance_metrics()
        
        # Capture multimodal analysis if requested
        multimodal_analysis = None
        if include_multimodal:
            try:
                multimodal_analysis = await self.multimodal_engine.analyze_page(include_screenshot=False)
                multimodal_analysis = multimodal_analysis.to_dict()
            except Exception as e:
                logger.warning(f"Multimodal analysis failed: {e}")
        
        # Create debug step
        step = DebugStep(
            step_id=self.current_step_id,
            timestamp=time.time() - self.recording_start_time,
            action_type=action_type,
            action_params=action_params,
            dom_snapshot=dom_snapshot,
            screenshot=screenshot,
            network_requests=self.network_requests.copy(),
            network_responses=self.network_responses.copy(),
            console_logs=self.console_logs.copy(),
            performance_metrics=performance_metrics,
            element_highlight=element_highlight,
            success=success,
            error_message=error_message,
            execution_time=execution_time,
            multimodal_analysis=multimodal_analysis
        )
        
        self.steps.append(step)
        self.current_step_id += 1
        
        # Clear step-specific logs
        self.network_requests = []
        self.network_responses = []
        self.console_logs = []
        
        logger.debug(f"Recorded step {step.step_id}: {action_type}")
    
    async def _capture_dom_snapshot(self) -> str:
        """Capture current DOM snapshot."""
        try:
            # Use DOM serializer to get simplified DOM
            dom_tree = await self.page.get_dom_tree()
            return json.dumps(dom_tree, indent=2)
        except Exception as e:
            logger.warning(f"Failed to capture DOM snapshot: {e}")
            return ""
    
    async def _capture_screenshot(self) -> Optional[str]:
        """Capture screenshot of current page."""
        try:
            screenshot = await self.page.screenshot()
            return base64.b64encode(screenshot).decode('utf-8')
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return None
    
    async def _capture_performance_metrics(self) -> Dict[str, float]:
        """Capture performance metrics."""
        try:
            metrics = await self.page.evaluate("""
                () => {
                    const perf = window.performance;
                    const timing = perf.timing;
                    
                    return {
                        fcp: perf.getEntriesByType('paint').find(e => e.name === 'first-contentful-paint')?.startTime || 0,
                        lcp: 0, // Would need LCP API
                        tti: 0, // Would need TTI calculation
                        network_latency: timing.responseEnd - timing.requestStart,
                        dom_content_loaded: timing.domContentLoadedEventEnd - timing.navigationStart,
                        load_event_end: timing.loadEventEnd - timing.navigationStart,
                        page_load_time: timing.loadEventEnd - timing.navigationStart
                    };
                }
            """)
            return metrics
        except Exception as e:
            logger.warning(f"Failed to capture performance metrics: {e}")
            return {}
    
    def add_network_request(self, request: Dict[str, Any]):
        """Add network request to current step."""
        self.network_requests.append(request)
    
    def add_network_response(self, response: Dict[str, Any]):
        """Add network response to current step."""
        self.network_responses.append(response)
    
    def add_console_log(self, log: Dict[str, Any]):
        """Add console log to current step."""
        self.console_logs.append(log)
    
    def get_debug_session(self) -> Dict[str, Any]:
        """Get complete debug session data."""
        return {
            "recording_start_time": self.recording_start_time,
            "total_steps": len(self.steps),
            "steps": [step.to_dict() for step in self.steps],
            "session_duration": time.time() - self.recording_start_time if self.is_recording else 0
        }
    
    async def generate_debug_report(self) -> str:
        """Generate a human-readable debug report."""
        report = []
        report.append("=" * 80)
        report.append("AUTOMATION DEBUG REPORT")
        report.append("=" * 80)
        report.append(f"Total Steps: {len(self.steps)}")
        report.append(f"Session Duration: {time.time() - self.recording_start_time:.2f}s")
        report.append("")
        
        for step in self.steps:
            report.append(f"Step {step.step_id}: {step.action_type}")
            report.append(f"  Timestamp: {step.timestamp:.2f}s")
            report.append(f"  Success: {step.success}")
            report.append(f"  Execution Time: {step.execution_time:.3f}s")
            
            if step.error_message:
                report.append(f"  Error: {step.error_message}")
            
            if step.multimodal_analysis:
                report.append(f"  Multimodal Analysis: {len(step.multimodal_analysis.get('visual_elements', []))} visual elements")
            
            report.append("")
        
        return "\n".join(report)


class Page:
    """Main page class with multimodal understanding capabilities."""
    
    def __init__(self, session: 'BrowserSession', target_id: str):
        self.session = session
        self.target_id = target_id
        self.dom_service = DomService(session, target_id)
        self.visual_debugger = VisualDebugger(self)
        self.multimodal_engine = MultiModalUnderstandingEngine(self)
        self._automation_patterns: Dict[str, AutomationPattern] = {}
        self._stealth_config = StealthConfig()
        self._performance_metrics = PerformanceMetrics()
        
    async def get_dom_tree(self) -> Dict[str, Any]:
        """Get the DOM tree of the page."""
        return await self.dom_service.get_dom_tree()
    
    async def analyze_page_multimodal(self, include_screenshot: bool = True) -> MultimodalPageAnalysis:
        """Perform comprehensive multimodal analysis of the page."""
        return await self.multimodal_engine.analyze_page(include_screenshot)
    
    async def get_visual_elements(self) -> List[MultimodalElement]:
        """Get all visual elements on the page."""
        analysis = await self.multimodal_engine.analyze_page(include_screenshot=False)
        return analysis.visual_elements
    
    async def extract_text_from_images(self) -> List[Dict[str, Any]]:
        """Extract text from all images on the page."""
        return await self.multimodal_engine._extract_text_from_images()
    
    async def recognize_charts(self) -> List[Dict[str, Any]]:
        """Recognize and extract data from charts on the page."""
        return await self.multimodal_engine._recognize_charts()
    
    async def parse_pdfs(self) -> List[Dict[str, Any]]:
        """Parse PDF content embedded in the page."""
        return await self.multimodal_engine._parse_pdfs()
    
    async def analyze_canvas_elements(self) -> List[Dict[str, Any]]:
        """Analyze canvas elements on the page."""
        return await self.multimodal_engine._analyze_canvas_elements()
    
    async def get_page_summary(self) -> str:
        """Get a summary of the page based on multimodal analysis."""
        analysis = await self.multimodal_engine.analyze_page(include_screenshot=False)
        return analysis.page_summary
    
    async def get_key_information(self) -> List[str]:
        """Extract key information from the page."""
        analysis = await self.multimodal_engine.analyze_page(include_screenshot=False)
        return analysis.key_information
    
    async def screenshot(self) -> bytes:
        """Capture screenshot of the page."""
        # Implementation would use CDP to capture screenshot
        raise NotImplementedError("Screenshot capture not implemented")
    
    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript expression in the page context."""
        # Implementation would use CDP Runtime.evaluate
        raise NotImplementedError("JavaScript evaluation not implemented")
    
    # Additional methods would be implemented here...
    # Including: click, type, scroll, navigate, etc.