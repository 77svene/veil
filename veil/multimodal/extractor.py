"""
veil/multimodal/extractor.py

Multi-Modal Understanding Engine for veil.
Processes DOM, visual content, PDFs, canvas elements, and embedded media.
Provides unified understanding combining DOM, visual, and semantic analysis.
"""

import asyncio
import base64
import io
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import numpy as np
from PIL import Image

# OCR and chart recognition imports
try:
    import pytesseract
    from pytesseract import Output
except ImportError:
    pytesseract = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pandas as pd
    from pandas import DataFrame
except ImportError:
    pd = None
    DataFrame = None

# Chart recognition libraries
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    go = None

try:
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
except ImportError:
    plt = None
    Figure = None

from ..actor.element import Element
from ..actor.page import Page


class ContentType(Enum):
    """Types of content that can be extracted."""
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    CANVAS = "canvas"
    CHART = "chart"
    VIDEO = "video"
    IFRAME = "iframe"
    SVG = "svg"
    MATH = "math"
    TABLE = "table"


@dataclass
class VisualElement:
    """Represents a visual element with its extracted content."""
    element_type: ContentType
    selector: str
    bbox: Dict[str, float]  # {x, y, width, height}
    content: Any  # Extracted content (text, data, etc.)
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "type": self.element_type.value,
            "selector": self.selector,
            "bbox": self.bbox,
            "content": self.content,
            "confidence": self.confidence,
            "metadata": self.metadata
        }


@dataclass
class ChartData:
    """Represents extracted chart data."""
    chart_type: str  # bar, line, pie, scatter, etc.
    title: Optional[str] = None
    x_axis_label: Optional[str] = None
    y_axis_label: Optional[str] = None
    data_points: List[Dict[str, Any]] = field(default_factory=list)
    series: List[Dict[str, Any]] = field(default_factory=list)
    legend: Optional[List[str]] = None
    source_element: Optional[str] = None
    
    def to_dataframe(self) -> Optional[DataFrame]:
        """Convert chart data to pandas DataFrame."""
        if pd is None:
            return None
        
        if self.data_points:
            return pd.DataFrame(self.data_points)
        elif self.series:
            # Combine all series into a single DataFrame
            dfs = []
            for i, series in enumerate(self.series):
                df = pd.DataFrame(series.get("data", []))
                df["series"] = series.get("name", f"Series_{i}")
                dfs.append(df)
            return pd.concat(dfs, ignore_index=True) if dfs else None
        return None


@dataclass
class PDFContent:
    """Represents extracted PDF content."""
    pages: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    text_content: str = ""
    images: List[VisualElement] = field(default_factory=list)
    tables: List[DataFrame] = field(default_factory=list)
    
    def get_page(self, page_num: int) -> Optional[Dict[str, Any]]:
        """Get content for a specific page."""
        if 0 <= page_num < len(self.pages):
            return self.pages[page_num]
        return None


class MultimodalExtractor:
    """
    Multi-Modal Understanding Engine for veil.
    
    Processes not just DOM but also visual content, PDFs, canvas elements,
    and embedded media. Provides unified understanding combining DOM, visual,
    and semantic analysis.
    """
    
    def __init__(self, 
                 page: Page,
                 ocr_enabled: bool = True,
                 chart_recognition_enabled: bool = True,
                 pdf_parsing_enabled: bool = True,
                 cache_dir: Optional[Path] = None):
        """
        Initialize the multimodal extractor.
        
        Args:
            page: The browser page to analyze
            ocr_enabled: Whether to enable OCR for text in images
            chart_recognition_enabled: Whether to enable chart recognition
            pdf_parsing_enabled: Whether to enable PDF parsing
            cache_dir: Directory for caching extracted content
        """
        self.page = page
        self.ocr_enabled = ocr_enabled and pytesseract is not None
        self.chart_recognition_enabled = chart_recognition_enabled
        self.pdf_parsing_enabled = pdf_parsing_enabled and fitz is not None
        self.cache_dir = cache_dir or Path.home() / ".veil" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
        self._visual_elements: List[VisualElement] = []
        self._dom_elements: List[Element] = []
        self._unified_content: Dict[str, Any] = {}
        
        # Initialize OCR if available
        if self.ocr_enabled:
            try:
                pytesseract.get_tesseract_version()
            except:
                self.logger.warning("Tesseract not found. OCR disabled.")
                self.ocr_enabled = False
    
    async def extract_all(self, 
                         selectors: Optional[List[str]] = None,
                         include_dom: bool = True,
                         include_visual: bool = True,
                         include_pdfs: bool = True) -> Dict[str, Any]:
        """
        Extract all multimodal content from the page.
        
        Args:
            selectors: Specific selectors to analyze (None for entire page)
            include_dom: Whether to include DOM analysis
            include_visual: Whether to include visual content analysis
            include_pdfs: Whether to include PDF content analysis
            
        Returns:
            Unified content dictionary with all extracted information
        """
        self.logger.info("Starting multimodal extraction")
        
        # Reset previous extraction
        self._visual_elements = []
        self._unified_content = {
            "url": self.page.url,
            "title": await self.page.title(),
            "timestamp": asyncio.get_event_loop().time(),
            "dom_elements": [],
            "visual_elements": [],
            "pdf_content": [],
            "charts": [],
            "semantic_content": {}
        }
        
        tasks = []
        
        if include_dom:
            tasks.append(self._extract_dom_elements(selectors))
        
        if include_visual:
            tasks.append(self._extract_visual_content(selectors))
        
        if include_pdfs and self.pdf_parsing_enabled:
            tasks.append(self._extract_pdf_content())
        
        # Run extraction tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"Extraction error: {result}")
            elif isinstance(result, list):
                # Handle list of visual elements
                for item in result:
                    if isinstance(item, VisualElement):
                        self._visual_elements.append(item)
        
        # Combine DOM and visual analysis
        await self._unify_content()
        
        # Extract semantic relationships
        await self._extract_semantic_relationships()
        
        self.logger.info(f"Extracted {len(self._visual_elements)} visual elements")
        return self._unified_content
    
    async def _extract_dom_elements(self, selectors: Optional[List[str]]) -> List[Element]:
        """Extract DOM elements using existing veil functionality."""
        elements = []
        
        if selectors:
            for selector in selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        elements.append(element)
                except Exception as e:
                    self.logger.warning(f"Failed to query selector {selector}: {e}")
        else:
            # Get all interactive and content elements
            try:
                # Use page's built-in element finding
                elements = await self.page.get_elements()
            except Exception as e:
                self.logger.warning(f"Failed to get elements: {e}")
        
        self._dom_elements = elements
        self._unified_content["dom_elements"] = [
            {
                "selector": el.selector,
                "tag": el.tag_name,
                "text": el.text_content,
                "attributes": el.attributes,
                "bbox": el.bounding_box
            }
            for el in elements
        ]
        
        return elements
    
    async def _extract_visual_content(self, selectors: Optional[List[str]]) -> List[VisualElement]:
        """Extract visual content including images, canvas, and embedded media."""
        visual_elements = []
        
        # Take screenshot for visual analysis
        screenshot = await self.page.screenshot()
        screenshot_img = Image.open(io.BytesIO(screenshot))
        
        # Find image elements
        image_selectors = selectors or ["img", "canvas", "svg", "video", "iframe", "embed", "object"]
        
        for selector in image_selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                for element in elements:
                    bbox = await element.bounding_box()
                    if not bbox:
                        continue
                    
                    # Extract content based on element type
                    tag = await element.evaluate("el => el.tagName.toLowerCase()")
                    
                    if tag == "img":
                        visual_element = await self._process_image_element(element, bbox, screenshot_img)
                    elif tag == "canvas":
                        visual_element = await self._process_canvas_element(element, bbox, screenshot_img)
                    elif tag == "svg":
                        visual_element = await self._process_svg_element(element, bbox)
                    elif tag == "video":
                        visual_element = await self._process_video_element(element, bbox)
                    elif tag in ["iframe", "embed", "object"]:
                        visual_element = await self._process_embedded_element(element, bbox)
                    else:
                        continue
                    
                    if visual_element:
                        visual_elements.append(visual_element)
                        
            except Exception as e:
                self.logger.warning(f"Failed to process selector {selector}: {e}")
        
        # Also run chart recognition on the entire page
        if self.chart_recognition_enabled:
            charts = await self._recognize_charts(screenshot_img)
            visual_elements.extend(charts)
        
        self._visual_elements.extend(visual_elements)
        return visual_elements
    
    async def _process_image_element(self, 
                                    element: Element, 
                                    bbox: Dict[str, float],
                                    full_screenshot: Image.Image) -> Optional[VisualElement]:
        """Process an image element with OCR."""
        try:
            # Get image source
            src = await element.get_attribute("src")
            alt = await element.get_attribute("alt") or ""
            
            # Extract text from image using OCR
            text_content = ""
            confidence = 0.0
            
            if self.ocr_enabled and src:
                # Crop image from screenshot
                img_crop = full_screenshot.crop((
                    bbox["x"], bbox["y"],
                    bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]
                ))
                
                # Run OCR
                ocr_data = pytesseract.image_to_data(img_crop, output_type=Output.DICT)
                text_parts = []
                confidences = []
                
                for i, text in enumerate(ocr_data["text"]):
                    if text.strip():
                        text_parts.append(text)
                        conf = int(ocr_data["conf"][i])
                        if conf > 0:
                            confidences.append(conf)
                
                text_content = " ".join(text_parts)
                confidence = np.mean(confidences) / 100.0 if confidences else 0.0
            
            return VisualElement(
                element_type=ContentType.IMAGE,
                selector=element.selector,
                bbox=bbox,
                content=text_content or alt,
                confidence=confidence,
                metadata={
                    "src": src,
                    "alt": alt,
                    "has_text": bool(text_content)
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to process image element: {e}")
            return None
    
    async def _process_canvas_element(self,
                                     element: Element,
                                     bbox: Dict[str, float],
                                     full_screenshot: Image.Image) -> Optional[VisualElement]:
        """Process a canvas element."""
        try:
            # Get canvas data if possible
            canvas_data = await element.evaluate("""el => {
                try {
                    return el.toDataURL();
                } catch (e) {
                    return null;
                }
            }""")
            
            # Extract text from canvas using OCR
            text_content = ""
            confidence = 0.0
            
            if self.ocr_enabled:
                img_crop = full_screenshot.crop((
                    bbox["x"], bbox["y"],
                    bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]
                ))
                
                ocr_data = pytesseract.image_to_data(img_crop, output_type=Output.DICT)
                text_parts = []
                confidences = []
                
                for i, text in enumerate(ocr_data["text"]):
                    if text.strip():
                        text_parts.append(text)
                        conf = int(ocr_data["conf"][i])
                        if conf > 0:
                            confidences.append(conf)
                
                text_content = " ".join(text_parts)
                confidence = np.mean(confidences) / 100.0 if confidences else 0.0
            
            # Try to detect if canvas contains a chart
            chart_data = None
            if self.chart_recognition_enabled and canvas_data:
                chart_data = await self._analyze_canvas_chart(canvas_data)
            
            return VisualElement(
                element_type=ContentType.CANVAS,
                selector=element.selector,
                bbox=bbox,
                content=text_content,
                confidence=confidence,
                metadata={
                    "has_data_url": bool(canvas_data),
                    "chart_data": chart_data,
                    "dimensions": {
                        "width": await element.evaluate("el => el.width"),
                        "height": await element.evaluate("el => el.height")
                    }
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to process canvas element: {e}")
            return None
    
    async def _process_svg_element(self, element: Element, bbox: Dict[str, float]) -> Optional[VisualElement]:
        """Process an SVG element."""
        try:
            # Get SVG content
            svg_content = await element.evaluate("el => el.outerHTML")
            
            # Extract text from SVG
            text_content = await element.evaluate("""el => {
                const texts = el.querySelectorAll('text');
                return Array.from(texts).map(t => t.textContent).join(' ');
            }""")
            
            return VisualElement(
                element_type=ContentType.SVG,
                selector=element.selector,
                bbox=bbox,
                content=text_content,
                metadata={
                    "svg_content": svg_content[:1000],  # Limit size
                    "has_text": bool(text_content)
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to process SVG element: {e}")
            return None
    
    async def _process_video_element(self, element: Element, bbox: Dict[str, float]) -> Optional[VisualElement]:
        """Process a video element."""
        try:
            src = await element.get_attribute("src")
            poster = await element.get_attribute("poster")
            
            return VisualElement(
                element_type=ContentType.VIDEO,
                selector=element.selector,
                bbox=bbox,
                content="",
                metadata={
                    "src": src,
                    "poster": poster,
                    "duration": await element.evaluate("el => el.duration || 0"),
                    "current_time": await element.evaluate("el => el.currentTime || 0")
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to process video element: {e}")
            return None
    
    async def _process_embedded_element(self, element: Element, bbox: Dict[str, float]) -> Optional[VisualElement]:
        """Process embedded content (iframes, embeds, objects)."""
        try:
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            src = await element.get_attribute("src") or await element.get_attribute("data")
            
            # Try to get content from same-origin iframes
            content = ""
            if tag == "iframe" and src:
                try:
                    # Check if same origin
                    parsed_src = urlparse(src)
                    parsed_page = urlparse(self.page.url)
                    
                    if parsed_src.netloc == parsed_page.netloc:
                        frame = await element.content_frame()
                        if frame:
                            content = await frame.evaluate("() => document.body.innerText")
                except:
                    pass
            
            return VisualElement(
                element_type=ContentType.IFRAME,
                selector=element.selector,
                bbox=bbox,
                content=content[:5000] if content else "",  # Limit content size
                metadata={
                    "tag": tag,
                    "src": src,
                    "sandbox": await element.get_attribute("sandbox")
                }
            )
        except Exception as e:
            self.logger.warning(f"Failed to process embedded element: {e}")
            return None
    
    async def _recognize_charts(self, image: Image.Image) -> List[VisualElement]:
        """Recognize charts in the screenshot."""
        charts = []
        
        if not self.chart_recognition_enabled:
            return charts
        
        try:
            # Convert to OpenCV format
            if cv2 is not None:
                img_array = np.array(image)
                img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                
                # Simple chart detection based on common patterns
                # This is a placeholder - in production, use a trained model
                gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                
                # Find contours that might be charts
                contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > 10000:  # Minimum area for a chart
                        x, y, w, h = cv2.boundingRect(contour)
                        
                        # Check aspect ratio (charts are usually wider than tall)
                        aspect_ratio = w / h
                        if 0.5 < aspect_ratio < 2.0:
                            # Extract chart region
                            chart_img = image.crop((x, y, x + w, y + h))
                            
                            # Analyze chart type
                            chart_type = await self._detect_chart_type(chart_img)
                            
                            charts.append(VisualElement(
                                element_type=ContentType.CHART,
                                selector=f"chart_at_{x}_{y}",
                                bbox={"x": x, "y": y, "width": w, "height": h},
                                content=chart_type,
                                confidence=0.7,  # Placeholder confidence
                                metadata={
                                    "chart_type": chart_type,
                                    "area": area,
                                    "aspect_ratio": aspect_ratio
                                }
                            ))
        except Exception as e:
            self.logger.warning(f"Chart recognition failed: {e}")
        
        return charts
    
    async def _detect_chart_type(self, image: Image.Image) -> str:
        """Detect the type of chart in an image."""
        # Placeholder implementation
        # In production, use a trained model or heuristic analysis
        
        try:
            if cv2 is not None:
                img_array = np.array(image)
                img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                
                # Count edges and lines
                edges = cv2.Canny(gray, 50, 150)
                lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=50, minLineLength=50, maxLineGap=10)
                
                if lines is not None:
                    num_lines = len(lines)
                    if num_lines > 10:
                        return "line"
                
                # Check for circular patterns (pie charts)
                circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=50,
                                          param1=50, param2=30, minRadius=10, maxRadius=100)
                if circles is not None:
                    return "pie"
                
                # Default to bar chart
                return "bar"
        except:
            pass
        
        return "unknown"
    
    async def _analyze_canvas_chart(self, data_url: str) -> Optional[ChartData]:
        """Analyze canvas content for chart data."""
        # This is a placeholder - in production, use specialized chart analysis
        return None
    
    async def _extract_pdf_content(self) -> List[PDFContent]:
        """Extract content from PDFs on the page."""
        pdf_contents = []
        
        if not self.pdf_parsing_enabled:
            return pdf_contents
        
        try:
            # Find PDF links
            pdf_links = await self.page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href$=".pdf"], embed[type="application/pdf"], object[type="application/pdf"]'));
                return links.map(link => ({
                    href: link.href || link.getAttribute('data'),
                    tag: link.tagName.toLowerCase()
                }));
            }""")
            
            for pdf_info in pdf_links:
                try:
                    pdf_content = await self._parse_pdf(pdf_info["href"])
                    if pdf_content:
                        pdf_contents.append(pdf_content)
                except Exception as e:
                    self.logger.warning(f"Failed to parse PDF {pdf_info['href']}: {e}")
        except Exception as e:
            self.logger.warning(f"Failed to extract PDF content: {e}")
        
        self._unified_content["pdf_content"] = [
            {
                "pages": len(pc.pages),
                "text_length": len(pc.text_content),
                "metadata": pc.metadata
            }
            for pc in pdf_contents
        ]
        
        return pdf_contents
    
    async def _parse_pdf(self, pdf_url: str) -> Optional[PDFContent]:
        """Parse a PDF document."""
        try:
            # Download PDF
            response = await self.page.evaluate(f"""async () => {{
                const response = await fetch('{pdf_url}');
                const buffer = await response.arrayBuffer();
                return Array.from(new Uint8Array(buffer));
            }}""")
            
            pdf_bytes = bytes(response)
            
            # Parse with PyMuPDF
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            pages = []
            full_text = ""
            images = []
            tables = []
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Extract text
                text = page.get_text()
                full_text += text + "\n"
                
                # Extract images
                image_list = page.get_images()
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    if base_image:
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Create visual element for the image
                        images.append(VisualElement(
                            element_type=ContentType.IMAGE,
                            selector=f"pdf_page_{page_num}_img_{img_index}",
                            bbox={"x": 0, "y": 0, "width": 0, "height": 0},  # PDF images don't have page coords
                            content=image_bytes,
                            metadata={
                                "page": page_num,
                                "format": image_ext,
                                "size": len(image_bytes)
                            }
                        ))
                
                # Try to extract tables
                if pd is not None:
                    try:
                        table_list = page.find_tables()
                        for table in table_list:
                            df = table.to_pandas()
                            tables.append(df)
                    except:
                        pass
                
                pages.append({
                    "page_num": page_num,
                    "text": text,
                    "width": page.rect.width,
                    "height": page.rect.height
                })
            
            doc.close()
            
            return PDFContent(
                pages=pages,
                metadata={
                    "title": doc.metadata.get("title", ""),
                    "author": doc.metadata.get("author", ""),
                    "pages": len(doc)
                },
                text_content=full_text,
                images=images,
                tables=tables
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse PDF: {e}")
            return None
    
    async def _unify_content(self):
        """Combine DOM and visual analysis into unified content."""
        # Create mapping between DOM elements and visual elements
        dom_by_selector = {el.selector: el for el in self._dom_elements}
        
        for visual_el in self._visual_elements:
            # Try to find corresponding DOM element
            dom_el = dom_by_selector.get(visual_el.selector)
            
            if dom_el:
                # Combine information
                self._unified_content["semantic_content"][visual_el.selector] = {
                    "dom_text": dom_el.text_content,
                    "visual_text": visual_el.content,
                    "element_type": visual_el.element_type.value,
                    "bbox": visual_el.bbox,
                    "confidence": visual_el.confidence,
                    "attributes": dom_el.attributes
                }
    
    async def _extract_semantic_relationships(self):
        """Extract semantic relationships between elements."""
        # Group elements by proximity and type
        elements_by_type = {}
        for visual_el in self._visual_elements:
            el_type = visual_el.element_type.value
            if el_type not in elements_by_type:
                elements_by_type[el_type] = []
            elements_by_type[el_type].append(visual_el)
        
        # Find related elements (e.g., label + input, chart + legend)
        relationships = []
        
        for el_type, elements in elements_by_type.items():
            if el_type == "image":
                # Look for captions near images
                for img_el in elements:
                    nearby_text = await self._find_nearby_text(img_el.bbox)
                    if nearby_text:
                        relationships.append({
                            "type": "image_caption",
                            "image": img_el.selector,
                            "caption": nearby_text,
                            "confidence": 0.8
                        })
        
        self._unified_content["relationships"] = relationships
    
    async def _find_nearby_text(self, bbox: Dict[str, float], radius: int = 50) -> Optional[str]:
        """Find text elements near a given bounding box."""
        try:
            # Find elements within radius
            nearby_elements = await self.page.evaluate(f"""() => {{
                const elements = [];
                const allElements = document.querySelectorAll('*');
                const targetRect = {{
                    x: {bbox['x']},
                    y: {bbox['y']},
                    width: {bbox['width']},
                    height: {bbox['height']}
                }};
                
                for (const el of allElements) {{
                    const rect = el.getBoundingClientRect();
                    const distance = Math.sqrt(
                        Math.pow(rect.x - targetRect.x, 2) +
                        Math.pow(rect.y - targetRect.y, 2)
                    );
                    
                    if (distance < {radius} && el.textContent.trim()) {{
                        elements.push(el.textContent.trim());
                    }}
                }}
                
                return elements.slice(0, 5);  // Limit to 5 nearby elements
            }}""")
            
            return " ".join(nearby_elements) if nearby_elements else None
        except:
            return None
    
    async def extract_chart_data(self, selector: str) -> Optional[ChartData]:
        """Extract data from a specific chart element."""
        try:
            element = await self.page.query_selector(selector)
            if not element:
                return None
            
            bbox = await element.bounding_box()
            if not bbox:
                return None
            
            # Take screenshot of the chart
            screenshot = await self.page.screenshot(clip=bbox)
            chart_img = Image.open(io.BytesIO(screenshot))
            
            # Analyze chart
            chart_type = await self._detect_chart_type(chart_img)
            
            # Try to extract data from canvas if applicable
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            if tag == "canvas":
                data_url = await element.evaluate("""el => {
                    try {
                        return el.toDataURL();
                    } catch (e) {
                        return null;
                    }
                }""")
                
                if data_url:
                    # Use chart recognition library if available
                    chart_data = await self._extract_data_from_chart_image(chart_img, chart_type)
                    return chart_data
            
            return ChartData(
                chart_type=chart_type,
                source_element=selector
            )
        except Exception as e:
            self.logger.warning(f"Failed to extract chart data: {e}")
            return None
    
    async def _extract_data_from_chart_image(self, image: Image.Image, chart_type: str) -> ChartData:
        """Extract data from a chart image using computer vision."""
        # Placeholder implementation
        # In production, use specialized chart data extraction
        
        return ChartData(
            chart_type=chart_type,
            data_points=[],
            series=[]
        )
    
    async def get_text_content(self, 
                              include_ocr: bool = True,
                              include_pdfs: bool = True) -> str:
        """Get all text content from the page."""
        text_parts = []
        
        # DOM text
        dom_text = await self.page.evaluate("() => document.body.innerText")
        text_parts.append(dom_text)
        
        # OCR text from images
        if include_ocr and self.ocr_enabled:
            for visual_el in self._visual_elements:
                if visual_el.element_type in [ContentType.IMAGE, ContentType.CANVAS]:
                    if visual_el.content and isinstance(visual_el.content, str):
                        text_parts.append(visual_el.content)
        
        # PDF text
        if include_pdfs and self.pdf_parsing_enabled:
            for pdf_content in self._unified_content.get("pdf_content", []):
                if "text_content" in pdf_content:
                    text_parts.append(pdf_content["text_content"])
        
        return "\n\n".join(text_parts)
    
    async def get_structured_content(self) -> Dict[str, Any]:
        """Get structured content representation."""
        return {
            "url": self._unified_content.get("url"),
            "title": self._unified_content.get("title"),
            "text_summary": await self.get_text_content()[:1000],  # First 1000 chars
            "element_count": {
                "dom": len(self._dom_elements),
                "visual": len(self._visual_elements)
            },
            "content_types": list(set(el.element_type.value for el in self._visual_elements)),
            "has_pdfs": bool(self._unified_content.get("pdf_content")),
            "has_charts": any(el.element_type == ContentType.CHART for el in self._visual_elements)
        }


# Factory function for easy integration
async def create_multimodal_extractor(page: Page, **kwargs) -> MultimodalExtractor:
    """Create and initialize a multimodal extractor for a page."""
    extractor = MultimodalExtractor(page, **kwargs)
    return extractor


# Integration with existing agent system
class MultimodalAgent:
    """
    Agent with multimodal understanding capabilities.
    Extends the existing agent system with visual content analysis.
    """
    
    def __init__(self, agent, extractor: MultimodalExtractor):
        """
        Initialize multimodal agent.
        
        Args:
            agent: Existing veil agent
            extractor: Multimodal extractor instance
        """
        self.agent = agent
        self.extractor = extractor
        self.logger = logging.getLogger(__name__)
    
    async def understand_page(self) -> Dict[str, Any]:
        """Get comprehensive understanding of the page."""
        # Extract multimodal content
        multimodal_content = await self.extractor.extract_all()
        
        # Get agent's existing understanding
        agent_state = await self.agent.get_state()
        
        # Combine understandings
        combined_understanding = {
            "agent_state": agent_state,
            "multimodal_content": multimodal_content,
            "structured_analysis": await self.extractor.get_structured_content(),
            "recommendations": await self._generate_recommendations(multimodal_content)
        }
        
        return combined_understanding
    
    async def _generate_recommendations(self, content: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on multimodal content."""
        recommendations = []
        
        # Check for accessibility issues
        images_without_alt = [
            el for el in content.get("visual_elements", [])
            if el.get("type") == "image" and not el.get("metadata", {}).get("alt")
        ]
        
        if images_without_alt:
            recommendations.append(
                f"Found {len(images_without_alt)} images without alt text. "
                "Consider adding alt attributes for accessibility."
            )
        
        # Check for interactive elements without proper labels
        if content.get("relationships"):
            recommendations.append(
                "Found semantic relationships between elements. "
                "Consider using ARIA labels for better accessibility."
            )
        
        return recommendations


# Example usage
if __name__ == "__main__":
    # This would be integrated into the existing veil workflow
    import asyncio
    from veil.actor.page import Page
    
    async def example_usage():
        # Assuming we have a page object
        page = None  # Would be initialized from veil
        
        # Create extractor
        extractor = MultimodalExtractor(
            page=page,
            ocr_enabled=True,
            chart_recognition_enabled=True,
            pdf_parsing_enabled=True
        )
        
        # Extract content
        content = await extractor.extract_all()
        
        # Get text content
        text = await extractor.get_text_content()
        
        # Get structured content
        structured = await extractor.get_structured_content()
        
        print(f"Extracted {len(content['visual_elements'])} visual elements")
        print(f"Text length: {len(text)} characters")
        print(f"Content types: {structured['content_types']}")
    
    # Run example
    # asyncio.run(example_usage())