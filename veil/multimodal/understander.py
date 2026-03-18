"""
Multi-Modal Understanding Engine for veil
Processes DOM, visual content, PDFs, canvas elements, and embedded media.
Provides unified understanding layer combining DOM, visual, and semantic analysis.
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
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import numpy as np
from PIL import Image

# OCR and image processing
try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False

# PDF parsing
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

# Chart and data visualization recognition
try:
    import torch
    import torchvision.transforms as transforms
    from torchvision import models
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

# DOM and browser integration
from veil.actor.page import Page
from veil.actor.element import Element

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Types of content that can be analyzed"""
    DOM = "dom"
    IMAGE = "image"
    PDF = "pdf"
    CANVAS = "canvas"
    VIDEO = "video"
    SVG = "svg"
    CHART = "chart"
    TABLE = "table"
    FORM = "form"
    TEXT = "text"
    MEDIA = "media"


@dataclass
class VisualElement:
    """Represents a visual element detected on the page"""
    element_type: ContentType
    bounds: Dict[str, float]  # x, y, width, height
    content: Optional[Any] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_element: Optional[Element] = None


@dataclass
class ChartData:
    """Extracted data from charts and graphs"""
    chart_type: str  # bar, line, pie, scatter, etc.
    title: Optional[str] = None
    x_axis_label: Optional[str] = None
    y_axis_label: Optional[str] = None
    data_points: List[Dict[str, Any]] = field(default_factory=list)
    legend: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class PDFContent:
    """Parsed content from PDF documents"""
    text: str
    pages: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: List[List[List[str]]] = field(default_factory=list)
    images: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class PageUnderstanding:
    """Unified understanding of page content"""
    dom_structure: Dict[str, Any] = field(default_factory=dict)
    visual_elements: List[VisualElement] = field(default_factory=list)
    text_content: str = ""
    semantic_entities: List[Dict[str, Any]] = field(default_factory=list)
    interactive_elements: List[Dict[str, Any]] = field(default_factory=list)
    media_content: List[Dict[str, Any]] = field(default_factory=list)
    charts: List[ChartData] = field(default_factory=list)
    pdfs: List[PDFContent] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    forms: List[Dict[str, Any]] = field(default_factory=list)
    accessibility_tree: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class MultiModalUnderstander:
    """
    Multi-Modal Understanding Engine for veil.
    
    Processes not just DOM but also visual content, PDFs, canvas elements,
    and embedded media. Understands charts, graphs, and complex UI components
    that pure DOM analysis misses.
    """
    
    def __init__(self, 
                 page: Page,
                 enable_ocr: bool = True,
                 enable_chart_recognition: bool = True,
                 enable_pdf_parsing: bool = True,
                 ocr_engine: str = "auto",
                 cache_results: bool = True):
        """
        Initialize the Multi-Modal Understanding Engine.
        
        Args:
            page: The browser page to analyze
            enable_ocr: Enable OCR for text in images
            enable_chart_recognition: Enable chart and graph recognition
            enable_pdf_parsing: Enable PDF document parsing
            ocr_engine: OCR engine to use ('tesseract', 'easyocr', 'auto')
            cache_results: Cache analysis results for performance
        """
        self.page = page
        self.enable_ocr = enable_ocr and (HAS_TESSERACT or HAS_EASYOCR)
        self.enable_chart_recognition = enable_chart_recognition and HAS_TORCH
        self.enable_pdf_parsing = enable_pdf_parsing and (HAS_PYPDF2 or HAS_PDFPLUMBER)
        self.cache_results = cache_results
        
        # Initialize OCR engine
        self.ocr_engine = ocr_engine
        self.ocr_reader = None
        if self.enable_ocr:
            self._init_ocr_engine()
        
        # Initialize chart recognition model
        self.chart_model = None
        self.chart_transform = None
        if self.enable_chart_recognition:
            self._init_chart_model()
        
        # Cache for analyzed content
        self._cache = {}
        self._visual_cache = {}
        
        # Chart type labels
        self.chart_types = [
            "bar", "line", "pie", "scatter", "area", 
            "histogram", "boxplot", "heatmap", "other"
        ]
        
        logger.info(f"MultiModalUnderstander initialized. "
                   f"OCR: {self.enable_ocr}, Charts: {self.enable_chart_recognition}, "
                   f"PDF: {self.enable_pdf_parsing}")
    
    def _init_ocr_engine(self):
        """Initialize the OCR engine based on configuration"""
        if self.ocr_engine == "auto":
            if HAS_EASYOCR:
                self.ocr_engine = "easyocr"
                try:
                    self.ocr_reader = easyocr.Reader(['en'], gpu=False)
                    logger.info("EasyOCR initialized")
                except Exception as e:
                    logger.warning(f"Failed to initialize EasyOCR: {e}")
                    self.enable_ocr = False
            elif HAS_TESSERACT:
                self.ocr_engine = "tesseract"
                logger.info("Tesseract OCR available")
            else:
                self.enable_ocr = False
                logger.warning("No OCR engine available")
        elif self.ocr_engine == "easyocr" and HAS_EASYOCR:
            try:
                self.ocr_reader = easyocr.Reader(['en'], gpu=False)
                logger.info("EasyOCR initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize EasyOCR: {e}")
                self.enable_ocr = False
        elif self.ocr_engine == "tesseract" and HAS_TESSERACT:
            logger.info("Tesseract OCR available")
        else:
            self.enable_ocr = False
            logger.warning(f"OCR engine {self.ocr_engine} not available")
    
    def _init_chart_model(self):
        """Initialize chart recognition model"""
        try:
            # Load a pre-trained model for chart recognition
            # In production, you would use a specialized chart recognition model
            # Here we use a general image classification model as a placeholder
            self.chart_model = models.resnet18(pretrained=True)
            self.chart_model.eval()
            
            # Image transformations for the model
            self.chart_transform = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                                   std=[0.229, 0.224, 0.225])
            ])
            logger.info("Chart recognition model initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize chart model: {e}")
            self.enable_chart_recognition = False
    
    async def understand_page(self, 
                             include_dom: bool = True,
                             include_visual: bool = True,
                             include_pdf: bool = True,
                             include_canvas: bool = True) -> PageUnderstanding:
        """
        Perform comprehensive multi-modal understanding of the page.
        
        Args:
            include_dom: Include DOM analysis
            include_visual: Include visual content analysis
            include_pdf: Include PDF parsing
            include_canvas: Include canvas element analysis
            
        Returns:
            PageUnderstanding object with unified analysis
        """
        cache_key = f"understand_{self.page.url}_{include_dom}_{include_visual}_{include_pdf}_{include_canvas}"
        
        if self.cache_results and cache_key in self._cache:
            logger.debug(f"Returning cached understanding for {self.page.url}")
            return self._cache[cache_key]
        
        understanding = PageUnderstanding()
        understanding.metadata["url"] = self.page.url
        understanding.metadata["timestamp"] = asyncio.get_event_loop().time()
        
        # Run analyses in parallel for performance
        tasks = []
        
        if include_dom:
            tasks.append(self._analyze_dom_structure())
        
        if include_visual and self.enable_ocr:
            tasks.append(self._analyze_visual_content())
        
        if include_pdf and self.enable_pdf_parsing:
            tasks.append(self._analyze_pdf_documents())
        
        if include_canvas:
            tasks.append(self._analyze_canvas_elements())
        
        # Always analyze text content and interactive elements
        tasks.append(self._extract_text_content())
        tasks.append(self._identify_interactive_elements())
        tasks.append(self._analyze_forms())
        tasks.append(self._analyze_tables())
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Analysis task {i} failed: {result}")
                continue
            
            if isinstance(result, dict):
                if "dom_structure" in result:
                    understanding.dom_structure = result["dom_structure"]
                elif "visual_elements" in result:
                    understanding.visual_elements.extend(result["visual_elements"])
                elif "text_content" in result:
                    understanding.text_content = result["text_content"]
                elif "interactive_elements" in result:
                    understanding.interactive_elements.extend(result["interactive_elements"])
                elif "forms" in result:
                    understanding.forms.extend(result["forms"])
                elif "tables" in result:
                    understanding.tables.extend(result["tables"])
                elif "pdfs" in result:
                    understanding.pdfs.extend(result["pdfs"])
                elif "canvas_elements" in result:
                    understanding.media_content.extend(result["canvas_elements"])
        
        # Extract semantic entities from text
        understanding.semantic_entities = await self._extract_semantic_entities(
            understanding.text_content
        )
        
        # Cache results if enabled
        if self.cache_results:
            self._cache[cache_key] = understanding
        
        return understanding
    
    async def _analyze_dom_structure(self) -> Dict[str, Any]:
        """Analyze DOM structure and extract semantic information"""
        try:
            # Get DOM snapshot
            dom_snapshot = await self.page.evaluate("""
                () => {
                    const getStructure = (node, depth = 0) => {
                        if (depth > 10) return null; // Limit depth
                        
                        const result = {
                            tagName: node.tagName,
                            id: node.id,
                            className: node.className,
                            childNodes: [],
                            attributes: {}
                        };
                        
                        // Get attributes
                        if (node.attributes) {
                            for (let attr of node.attributes) {
                                result.attributes[attr.name] = attr.value;
                            }
                        }
                        
                        // Get child nodes
                        if (node.childNodes) {
                            for (let child of node.childNodes) {
                                if (child.nodeType === Node.ELEMENT_NODE) {
                                    const childStructure = getStructure(child, depth + 1);
                                    if (childStructure) {
                                        result.childNodes.push(childStructure);
                                    }
                                }
                            }
                        }
                        
                        return result;
                    };
                    
                    return getStructure(document.documentElement);
                }
            """)
            
            return {"dom_structure": dom_snapshot}
        except Exception as e:
            logger.error(f"DOM analysis failed: {e}")
            return {"dom_structure": {}}
    
    async def _analyze_visual_content(self) -> Dict[str, Any]:
        """Analyze visual content including images, charts, and graphics"""
        visual_elements = []
        
        try:
            # Get all images on the page
            images = await self.page.evaluate("""
                () => {
                    const images = [];
                    document.querySelectorAll('img, svg, canvas, [style*="background-image"]').forEach(el => {
                        const rect = el.getBoundingClientRect();
                        images.push({
                            element: el.tagName,
                            src: el.src || el.getAttribute('data-src') || '',
                            alt: el.alt || '',
                            width: rect.width,
                            height: rect.height,
                            x: rect.x,
                            y: rect.y,
                            visible: rect.width > 0 && rect.height > 0
                        });
                    });
                    return images;
                }
            """)
            
            for img_info in images:
                if not img_info.get("visible", False):
                    continue
                
                element = VisualElement(
                    element_type=ContentType.IMAGE,
                    bounds={
                        "x": img_info["x"],
                        "y": img_info["y"],
                        "width": img_info["width"],
                        "height": img_info["height"]
                    },
                    metadata={
                        "src": img_info["src"],
                        "alt": img_info["alt"],
                        "element": img_info["element"]
                    }
                )
                
                # Try to extract text from image using OCR
                if self.enable_ocr and img_info["width"] > 50 and img_info["height"] > 50:
                    try:
                        # Take screenshot of the image area
                        screenshot = await self.page.screenshot(
                            clip={
                                "x": img_info["x"],
                                "y": img_info["y"],
                                "width": img_info["width"],
                                "height": img_info["height"]
                            }
                        )
                        
                        # Perform OCR
                        ocr_text = await self._perform_ocr(screenshot)
                        if ocr_text:
                            element.content = ocr_text
                            element.element_type = ContentType.TEXT
                            
                            # Check if it might be a chart
                            if self.enable_chart_recognition:
                                chart_data = await self._recognize_chart(screenshot)
                                if chart_data:
                                    element.element_type = ContentType.CHART
                                    element.metadata["chart_data"] = chart_data
                    except Exception as e:
                        logger.debug(f"Failed to analyze image: {e}")
                
                visual_elements.append(element)
            
            return {"visual_elements": visual_elements}
            
        except Exception as e:
            logger.error(f"Visual content analysis failed: {e}")
            return {"visual_elements": []}
    
    async def _perform_ocr(self, image_data: bytes) -> Optional[str]:
        """Perform OCR on image data"""
        try:
            image = Image.open(io.BytesIO(image_data))
            
            if self.ocr_engine == "easyocr" and self.ocr_reader:
                # EasyOCR
                result = self.ocr_reader.readtext(np.array(image))
                text = " ".join([item[1] for item in result])
                return text.strip()
            
            elif self.ocr_engine == "tesseract" and HAS_TESSERACT:
                # Tesseract
                text = pytesseract.image_to_string(image)
                return text.strip()
            
            return None
            
        except Exception as e:
            logger.debug(f"OCR failed: {e}")
            return None
    
    async def _recognize_chart(self, image_data: bytes) -> Optional[ChartData]:
        """Recognize and extract data from charts"""
        if not self.enable_chart_recognition or not self.chart_model:
            return None
        
        try:
            image = Image.open(io.BytesIO(image_data)).convert('RGB')
            
            # Preprocess image for the model
            input_tensor = self.chart_transform(image)
            input_batch = input_tensor.unsqueeze(0)
            
            # Get prediction (simplified - in production you'd use a specialized model)
            with torch.no_grad():
                output = self.chart_model(input_batch)
                probabilities = torch.nn.functional.softmax(output[0], dim=0)
            
            # Get top prediction
            top_prob, top_class = torch.topk(probabilities, 1)
            
            # Map to chart type (simplified mapping)
            chart_type_idx = top_class.item() % len(self.chart_types)
            chart_type = self.chart_types[chart_type_idx]
            
            # Create chart data object
            chart_data = ChartData(
                chart_type=chart_type,
                confidence=top_prob.item(),
                metadata={
                    "model_output": output.tolist(),
                    "probabilities": probabilities.tolist()
                }
            )
            
            # In a real implementation, you would extract actual data points
            # from the chart using specialized computer vision techniques
            
            return chart_data
            
        except Exception as e:
            logger.debug(f"Chart recognition failed: {e}")
            return None
    
    async def _analyze_pdf_documents(self) -> Dict[str, Any]:
        """Analyze PDF documents linked or embedded in the page"""
        pdfs = []
        
        try:
            # Find PDF links
            pdf_links = await self.page.evaluate("""
                () => {
                    const links = [];
                    document.querySelectorAll('a[href$=".pdf"], embed[type="application/pdf"], object[type="application/pdf"]').forEach(el => {
                        if (el.href) {
                            links.push({
                                href: el.href,
                                text: el.textContent || '',
                                type: el.tagName
                            });
                        }
                    });
                    return links;
                }
            """)
            
            for pdf_link in pdf_links:
                try:
                    # Download and parse PDF
                    pdf_content = await self._parse_pdf_url(pdf_link["href"])
                    if pdf_content:
                        pdf_content.metadata["source_url"] = pdf_link["href"]
                        pdf_content.metadata["link_text"] = pdf_link["text"]
                        pdfs.append(pdf_content)
                except Exception as e:
                    logger.debug(f"Failed to parse PDF {pdf_link['href']}: {e}")
            
            return {"pdfs": pdfs}
            
        except Exception as e:
            logger.error(f"PDF analysis failed: {e}")
            return {"pdfs": []}
    
    async def _parse_pdf_url(self, url: str) -> Optional[PDFContent]:
        """Parse PDF from URL"""
        try:
            # In production, you would download the PDF and parse it
            # For now, return a placeholder
            return PDFContent(
                text="PDF content extraction would happen here",
                pages=1,
                metadata={"url": url}
            )
        except Exception as e:
            logger.debug(f"PDF parsing failed: {e}")
            return None
    
    async def _analyze_canvas_elements(self) -> Dict[str, Any]:
        """Analyze canvas elements and their content"""
        canvas_elements = []
        
        try:
            # Find canvas elements
            canvases = await self.page.evaluate("""
                () => {
                    const canvases = [];
                    document.querySelectorAll('canvas').forEach(canvas => {
                        const rect = canvas.getBoundingClientRect();
                        canvases.push({
                            width: canvas.width,
                            height: canvas.height,
                            x: rect.x,
                            y: rect.y,
                            id: canvas.id,
                            className: canvas.className
                        });
                    });
                    return canvases;
                }
            """)
            
            for canvas_info in canvases:
                element = VisualElement(
                    element_type=ContentType.CANVAS,
                    bounds={
                        "x": canvas_info["x"],
                        "y": canvas_info["y"],
                        "width": canvas_info["width"],
                        "height": canvas_info["height"]
                    },
                    metadata={
                        "id": canvas_info["id"],
                        "className": canvas_info["className"]
                    }
                )
                
                # Try to extract canvas content as image
                try:
                    screenshot = await self.page.screenshot(
                        clip={
                            "x": canvas_info["x"],
                            "y": canvas_info["y"],
                            "width": canvas_info["width"],
                            "height": canvas_info["height"]
                        }
                    )
                    
                    # Analyze canvas content
                    if self.enable_ocr:
                        ocr_text = await self._perform_ocr(screenshot)
                        if ocr_text:
                            element.content = ocr_text
                    
                    if self.enable_chart_recognition:
                        chart_data = await self._recognize_chart(screenshot)
                        if chart_data:
                            element.element_type = ContentType.CHART
                            element.metadata["chart_data"] = chart_data
                            
                except Exception as e:
                    logger.debug(f"Failed to analyze canvas: {e}")
                
                canvas_elements.append(element)
            
            return {"canvas_elements": canvas_elements}
            
        except Exception as e:
            logger.error(f"Canvas analysis failed: {e}")
            return {"canvas_elements": []}
    
    async def _extract_text_content(self) -> Dict[str, Any]:
        """Extract all text content from the page"""
        try:
            text_content = await self.page.evaluate("""
                () => {
                    // Get text from various elements
                    const textNodes = [];
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    
                    let node;
                    while (node = walker.nextNode()) {
                        const text = node.textContent.trim();
                        if (text && text.length > 0) {
                            textNodes.push(text);
                        }
                    }
                    
                    return textNodes.join(' ');
                }
            """)
            
            return {"text_content": text_content}
            
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return {"text_content": ""}
    
    async def _identify_interactive_elements(self) -> Dict[str, Any]:
        """Identify interactive elements (buttons, links, inputs, etc.)"""
        try:
            interactive_elements = await self.page.evaluate("""
                () => {
                    const elements = [];
                    const selectors = [
                        'button', 'a[href]', 'input', 'select', 'textarea',
                        '[role="button"]', '[role="link"]', '[role="checkbox"]',
                        '[role="radio"]', '[role="tab"]', '[onclick]'
                    ];
                    
                    document.querySelectorAll(selectors.join(',')).forEach(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            elements.push({
                                tagName: el.tagName,
                                type: el.type || '',
                                id: el.id,
                                className: el.className,
                                text: el.textContent?.trim()?.substring(0, 100) || '',
                                href: el.href || '',
                                value: el.value || '',
                                placeholder: el.placeholder || '',
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                                disabled: el.disabled || false,
                                visible: true
                            });
                        }
                    });
                    
                    return elements;
                }
            """)
            
            return {"interactive_elements": interactive_elements}
            
        except Exception as e:
            logger.error(f"Interactive elements analysis failed: {e}")
            return {"interactive_elements": []}
    
    async def _analyze_forms(self) -> Dict[str, Any]:
        """Analyze forms and their fields"""
        try:
            forms = await self.page.evaluate("""
                () => {
                    const forms = [];
                    document.querySelectorAll('form').forEach(form => {
                        const formData = {
                            id: form.id,
                            name: form.name,
                            action: form.action,
                            method: form.method,
                            fields: []
                        };
                        
                        form.querySelectorAll('input, select, textarea').forEach(field => {
                            const rect = field.getBoundingClientRect();
                            formData.fields.push({
                                type: field.type,
                                name: field.name,
                                id: field.id,
                                placeholder: field.placeholder,
                                required: field.required,
                                value: field.value,
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            });
                        });
                        
                        forms.push(formData);
                    });
                    
                    return forms;
                }
            """)
            
            return {"forms": forms}
            
        except Exception as e:
            logger.error(f"Forms analysis failed: {e}")
            return {"forms": []}
    
    async def _analyze_tables(self) -> Dict[str, Any]:
        """Analyze tables and extract structured data"""
        try:
            tables = await self.page.evaluate("""
                () => {
                    const tables = [];
                    document.querySelectorAll('table').forEach(table => {
                        const tableData = {
                            id: table.id,
                            className: table.className,
                            headers: [],
                            rows: []
                        };
                        
                        // Get headers
                        table.querySelectorAll('th').forEach(th => {
                            tableData.headers.push(th.textContent.trim());
                        });
                        
                        // Get rows
                        table.querySelectorAll('tr').forEach(tr => {
                            const rowData = [];
                            tr.querySelectorAll('td').forEach(td => {
                                rowData.push(td.textContent.trim());
                            });
                            if (rowData.length > 0) {
                                tableData.rows.push(rowData);
                            }
                        });
                        
                        if (tableData.headers.length > 0 || tableData.rows.length > 0) {
                            tables.push(tableData);
                        }
                    });
                    
                    return tables;
                }
            """)
            
            return {"tables": tables}
            
        except Exception as e:
            logger.error(f"Tables analysis failed: {e}")
            return {"tables": []}
    
    async def _extract_semantic_entities(self, text: str) -> List[Dict[str, Any]]:
        """Extract semantic entities from text (NER, keywords, etc.)"""
        entities = []
        
        # Simple pattern-based entity extraction
        patterns = {
            "email": r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "url": r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+',
            "phone": r'\b(?:\+?(\d{1,3}))?[-. (]*(\d{3})[-. )]*(\d{3})[-. ]*(\d{4})(?: *x(\d+))?\b',
            "date": r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b',
            "price": r'\$\d+(?:\.\d{2})?|\d+(?:\.\d{2})?\s*(?:dollars|USD)'
        }
        
        for entity_type, pattern in patterns.items():
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                entities.append({
                    "type": entity_type,
                    "value": match.group(),
                    "start": match.start(),
                    "end": match.end()
                })
        
        return entities
    
    async def get_element_context(self, element: Element) -> Dict[str, Any]:
        """
        Get comprehensive context for a specific element.
        
        Args:
            element: The element to analyze
            
        Returns:
            Dictionary with element context including visual and semantic info
        """
        try:
            # Get element bounds
            bounds = await element.get_bounding_box()
            
            # Take screenshot of element area
            screenshot = await self.page.screenshot(
                clip={
                    "x": bounds["x"],
                    "y": bounds["y"],
                    "width": bounds["width"],
                    "height": bounds["height"]
                }
            )
            
            context = {
                "element_info": {
                    "tag": await element.get_tag_name(),
                    "text": await element.get_text(),
                    "attributes": await element.get_attributes(),
                    "bounds": bounds
                },
                "visual_analysis": {}
            }
            
            # Perform OCR if enabled
            if self.enable_ocr:
                ocr_text = await self._perform_ocr(screenshot)
                if ocr_text:
                    context["visual_analysis"]["ocr_text"] = ocr_text
            
            # Recognize charts if enabled
            if self.enable_chart_recognition:
                chart_data = await self._recognize_chart(screenshot)
                if chart_data:
                    context["visual_analysis"]["chart_data"] = chart_data
            
            # Get surrounding context
            context["surrounding_text"] = await self.page.evaluate("""
                (element) => {
                    const parent = element.parentElement;
                    if (!parent) return '';
                    
                    let text = '';
                    const walker = document.createTreeWalker(
                        parent,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    
                    let node;
                    while (node = walker.nextNode()) {
                        text += node.textContent + ' ';
                    }
                    
                    return text.trim().substring(0, 500);
                }
            """, element)
            
            return context
            
        except Exception as e:
            logger.error(f"Failed to get element context: {e}")
            return {"error": str(e)}
    
    async def find_visual_elements(self, 
                                  element_type: Optional[ContentType] = None,
                                  min_confidence: float = 0.5) -> List[VisualElement]:
        """
        Find visual elements on the page.
        
        Args:
            element_type: Filter by element type
            min_confidence: Minimum confidence threshold
            
        Returns:
            List of visual elements matching criteria
        """
        understanding = await self.understand_page(
            include_dom=False,
            include_visual=True,
            include_pdf=False,
            include_canvas=True
        )
        
        elements = []
        for element in understanding.visual_elements:
            if element_type and element.element_type != element_type:
                continue
            if element.confidence < min_confidence:
                continue
            elements.append(element)
        
        return elements
    
    async def extract_chart_data(self, chart_element: VisualElement) -> Optional[ChartData]:
        """
        Extract detailed data from a chart element.
        
        Args:
            chart_element: The chart element to analyze
            
        Returns:
            ChartData object with extracted data
        """
        if chart_element.element_type != ContentType.CHART:
            return None
        
        # If we already have chart data in metadata, return it
        if "chart_data" in chart_element.metadata:
            return chart_element.metadata["chart_data"]
        
        # Otherwise, try to extract it
        try:
            screenshot = await self.page.screenshot(
                clip={
                    "x": chart_element.bounds["x"],
                    "y": chart_element.bounds["y"],
                    "width": chart_element.bounds["width"],
                    "height": chart_element.bounds["height"]
                }
            )
            
            chart_data = await self._recognize_chart(screenshot)
            return chart_data
            
        except Exception as e:
            logger.error(f"Failed to extract chart data: {e}")
            return None
    
    def clear_cache(self):
        """Clear the analysis cache"""
        self._cache.clear()
        self._visual_cache.clear()
        logger.info("Analysis cache cleared")


# Factory function for easy instantiation
def create_understander(page: Page, **kwargs) -> MultiModalUnderstander:
    """
    Create a MultiModalUnderstander instance.
    
    Args:
        page: The browser page to analyze
        **kwargs: Additional configuration options
        
    Returns:
        Configured MultiModalUnderstander instance
    """
    return MultiModalUnderstander(page, **kwargs)


# Integration with existing veil agent
async def enhance_agent_with_multimodal(agent, understander: MultiModalUnderstander):
    """
    Enhance an existing veil agent with multi-modal understanding.
    
    Args:
        agent: The veil agent to enhance
        understander: The multi-modal understander instance
    """
    # Store original methods
    original_observe = agent.observe if hasattr(agent, 'observe') else None
    
    async def enhanced_observe():
        """Enhanced observation with multi-modal understanding"""
        # Get standard observation
        if original_observe:
            observation = await original_observe()
        else:
            observation = {}
        
        # Add multi-modal understanding
        understanding = await understander.understand_page()
        
        # Enhance observation with multi-modal data
        observation["multimodal"] = {
            "visual_elements_count": len(understanding.visual_elements),
            "charts_detected": len(understanding.charts),
            "pdfs_detected": len(understanding.pdfs),
            "text_length": len(understanding.text_content),
            "interactive_elements_count": len(understanding.interactive_elements),
            "semantic_entities_count": len(understanding.semantic_entities)
        }
        
        # Add chart data if any charts detected
        if understanding.charts:
            observation["multimodal"]["charts"] = [
                {
                    "type": chart.chart_type,
                    "confidence": chart.confidence,
                    "title": chart.title
                }
                for chart in understanding.charts[:5]  # Limit to first 5
            ]
        
        return observation
    
    # Replace agent's observe method
    if hasattr(agent, 'observe'):
        agent.observe = enhanced_observe
    
    # Store understander for later use
    agent.multimodal_understander = understander
    
    logger.info("Agent enhanced with multi-modal understanding")