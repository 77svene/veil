"""
Adaptive Element Detection with Visual AI - Layout Analyzer

Replaces static DOM selectors with visual AI that understands page layout semantically.
Uses computer vision to detect interactive elements even when hidden in shadow DOM,
dynamically loaded, or styled unusually.
"""

import asyncio
import base64
import io
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image

from veil.actor.element import Element
from veil.actor.page import Page

logger = logging.getLogger(__name__)


class ElementType(Enum):
    """Types of interactive elements that can be detected."""
    BUTTON = "button"
    LINK = "link"
    INPUT = "input"
    TEXTAREA = "textarea"
    SELECT = "select"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    TOGGLE = "toggle"
    SLIDER = "slider"
    DROPDOWN = "dropdown"
    MENU_ITEM = "menu_item"
    TAB = "tab"
    ICON_BUTTON = "icon_button"
    IMAGE_BUTTON = "image_button"
    CUSTOM_INTERACTIVE = "custom_interactive"


class DetectionConfidence(Enum):
    """Confidence levels for element detection."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


@dataclass
class VisualElement:
    """Represents an element detected through visual analysis."""
    element_type: ElementType
    bounding_box: Tuple[int, int, int, int]  # x, y, width, height
    confidence: DetectionConfidence
    confidence_score: float  # 0.0 to 1.0
    text_content: Optional[str] = None
    visual_features: Dict[str, Any] = field(default_factory=dict)
    dom_element: Optional[Element] = None
    selector: Optional[str] = None
    is_shadow_dom: bool = False
    is_dynamically_loaded: bool = False
    accessibility_label: Optional[str] = None
    
    @property
    def center(self) -> Tuple[int, int]:
        """Get center coordinates of the element."""
        x, y, w, h = self.bounding_box
        return (x + w // 2, y + h // 2)
    
    @property
    def area(self) -> int:
        """Get area of the element in pixels."""
        _, _, w, h = self.bounding_box
        return w * h


@dataclass
class LayoutAnalysisResult:
    """Result of layout analysis containing detected elements and metadata."""
    elements: List[VisualElement]
    page_width: int
    page_height: int
    viewport_width: int
    viewport_height: int
    scroll_position: Tuple[int, int]
    analysis_time_ms: float
    dom_elements_analyzed: int
    visual_elements_detected: int
    hybrid_matches: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class VisualModelInterface:
    """Interface for visual AI models used in layout analysis."""
    
    def __init__(self, model_name: str = "clip"):
        self.model_name = model_name
        self.model = None
        self.preprocessor = None
        
    async def initialize(self):
        """Initialize the visual model."""
        try:
            if self.model_name == "clip":
                await self._initialize_clip()
            elif self.model_name == "florence":
                await self._initialize_florence()
            elif self.model_name == "yolo":
                await self._initialize_yolo()
            else:
                raise ValueError(f"Unsupported model: {self.model_name}")
        except ImportError as e:
            logger.warning(f"Could not import required packages for {self.model_name}: {e}")
            logger.info("Falling back to heuristic detection")
            self.model = None
    
    async def _initialize_clip(self):
        """Initialize CLIP model for visual understanding."""
        try:
            import torch
            import clip
            from PIL import Image
            
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model, self.preprocessor = clip.load("ViT-B/32", device=self.device)
            logger.info(f"CLIP model loaded on {self.device}")
        except Exception as e:
            logger.error(f"Failed to initialize CLIP: {e}")
            raise
    
    async def _initialize_florence(self):
        """Initialize Florence model for UI understanding."""
        try:
            # Florence would be initialized here
            # This is a placeholder for actual Florence integration
            logger.info("Florence model initialization placeholder")
            self.model = "florence_placeholder"
        except Exception as e:
            logger.error(f"Failed to initialize Florence: {e}")
            raise
    
    async def _initialize_yolo(self):
        """Initialize YOLO model for object detection."""
        try:
            from ultralytics import YOLO
            
            # Load a pretrained YOLO model
            self.model = YOLO('yolov8n.pt')
            logger.info("YOLO model loaded")
        except Exception as e:
            logger.error(f"Failed to initialize YOLO: {e}")
            raise
    
    async def detect_elements(self, image: Image.Image) -> List[Dict[str, Any]]:
        """
        Detect interactive elements in an image.
        
        Args:
            image: PIL Image of the webpage
            
        Returns:
            List of detected elements with bounding boxes and confidence
        """
        if self.model is None:
            return await self._heuristic_detection(image)
        
        if self.model_name == "clip":
            return await self._detect_with_clip(image)
        elif self.model_name == "florence":
            return await self._detect_with_florence(image)
        elif self.model_name == "yolo":
            return await self._detect_with_yolo(image)
        else:
            return await self._heuristic_detection(image)
    
    async def _detect_with_clip(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Detect elements using CLIP model."""
        try:
            import torch
            import clip
            
            # Define text prompts for different element types
            element_prompts = [
                "a clickable button",
                "a hyperlink or link",
                "a text input field",
                "a dropdown menu",
                "a checkbox",
                "a radio button",
                "a slider control",
                "a toggle switch",
                "a navigation tab",
                "an icon button",
                "an image that can be clicked",
                "a form submit button",
                "a menu item",
                "a search box",
                "a login button"
            ]
            
            # Preprocess image
            image_input = self.preprocessor(image).unsqueeze(0).to(self.device)
            
            # Tokenize text prompts
            text_inputs = clip.tokenize(element_prompts).to(self.device)
            
            # Calculate similarity
            with torch.no_grad():
                image_features = self.model.encode_image(image_input)
                text_features = self.model.encode_text(text_inputs)
                
                # Normalize features
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                
                # Calculate similarity scores
                similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
                values, indices = similarity[0].topk(5)
            
            # For CLIP, we'd need additional object detection to get bounding boxes
            # This is a simplified version - in production, combine with object detection
            detected_elements = []
            
            # Use heuristics to find potential interactive regions
            regions = await self._find_interactive_regions(image)
            
            for region in regions:
                # Analyze each region with CLIP
                region_image = image.crop(region)
                region_input = self.preprocessor(region_image).unsqueeze(0).to(self.device)
                
                with torch.no_grad():
                    region_features = self.model.encode_image(region_input)
                    region_features = region_features / region_features.norm(dim=-1, keepdim=True)
                    region_similarity = (100.0 * region_features @ text_features.T).softmax(dim=-1)
                    region_values, region_indices = region_similarity[0].topk(1)
                
                if region_values[0] > 0.3:  # Confidence threshold
                    element_type = self._map_prompt_to_element_type(element_prompts[region_indices[0]])
                    detected_elements.append({
                        "bounding_box": region,
                        "element_type": element_type,
                        "confidence": float(region_values[0]),
                        "text": None
                    })
            
            return detected_elements
            
        except Exception as e:
            logger.error(f"CLIP detection failed: {e}")
            return []
    
    async def _detect_with_florence(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Detect elements using Florence model."""
        # Placeholder for Florence integration
        # Florence would provide more accurate UI element detection
        logger.info("Using Florence for detection (placeholder)")
        return await self._heuristic_detection(image)
    
    async def _detect_with_yolo(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Detect elements using YOLO model."""
        try:
            import numpy as np
            
            # Convert PIL image to numpy array
            img_array = np.array(image)
            
            # Run inference
            results = self.model(img_array)
            
            detected_elements = []
            
            for result in results:
                boxes = result.boxes
                if boxes is not None:
                    for box in boxes:
                        # Get bounding box coordinates
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        confidence = float(box.conf[0])
                        class_id = int(box.cls[0])
                        
                        # Map YOLO classes to our element types
                        # YOLO is trained on COCO, so we need to map common objects
                        # that might be interactive (like cell phone, remote, etc.)
                        element_type = self._map_yolo_class_to_element(class_id)
                        
                        if element_type and confidence > 0.5:
                            detected_elements.append({
                                "bounding_box": (int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                                "element_type": element_type,
                                "confidence": confidence,
                                "text": None
                            })
            
            return detected_elements
            
        except Exception as e:
            logger.error(f"YOLO detection failed: {e}")
            return []
    
    async def _heuristic_detection(self, image: Image.Image) -> List[Dict[str, Any]]:
        """Fallback heuristic detection when AI models are unavailable."""
        try:
            import cv2
            import numpy as np
            
            # Convert to OpenCV format
            img_array = np.array(image)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # Edge detection
            edges = cv2.Canny(gray, 50, 150)
            
            # Find contours
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            detected_elements = []
            
            for contour in contours:
                # Get bounding rectangle
                x, y, w, h = cv2.boundingRect(contour)
                
                # Filter by size - interactive elements are usually not too small or too large
                if 20 < w < 500 and 20 < h < 200:
                    # Calculate aspect ratio
                    aspect_ratio = w / h
                    
                    # Determine element type based on heuristics
                    element_type = self._classify_by_heuristics(w, h, aspect_ratio, gray[y:y+h, x:x+w])
                    
                    if element_type:
                        detected_elements.append({
                            "bounding_box": (x, y, w, h),
                            "element_type": element_type,
                            "confidence": 0.6,  # Medium confidence for heuristic detection
                            "text": None
                        })
            
            return detected_elements
            
        except Exception as e:
            logger.error(f"Heuristic detection failed: {e}")
            return []
    
    async def _find_interactive_regions(self, image: Image.Image) -> List[Tuple[int, int, int, int]]:
        """Find regions likely to contain interactive elements."""
        try:
            import cv2
            import numpy as np
            
            img_array = np.array(image)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            
            # Look for rectangular regions with specific characteristics
            regions = []
            
            # Method 1: Edge-based detection
            edges = cv2.Canny(gray, 30, 100)
            contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                # Approximate the contour
                epsilon = 0.02 * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)
                
                # If it's a rectangle (4 vertices)
                if len(approx) == 4:
                    x, y, w, h = cv2.boundingRect(approx)
                    
                    # Filter by size and aspect ratio
                    if 30 < w < 600 and 20 < h < 150:
                        aspect_ratio = w / h
                        if 0.5 < aspect_ratio < 10:  # Reasonable aspect ratios for buttons/inputs
                            regions.append((x, y, w, h))
            
            # Method 2: Color-based detection (look for common button colors)
            hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
            
            # Define color ranges for common interactive elements
            color_ranges = [
                # Blue buttons
                (np.array([100, 50, 50]), np.array([130, 255, 255])),
                # Green buttons
                (np.array([40, 50, 50]), np.array([80, 255, 255])),
                # Red buttons
                (np.array([0, 50, 50]), np.array([10, 255, 255])),
                # Gray buttons
                (np.array([0, 0, 100]), np.array([180, 30, 200]))
            ]
            
            for lower, upper in color_ranges:
                mask = cv2.inRange(hsv, lower, upper)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                for contour in contours:
                    if cv2.contourArea(contour) > 500:  # Minimum area
                        x, y, w, h = cv2.boundingRect(contour)
                        if 30 < w < 600 and 20 < h < 150:
                            regions.append((x, y, w, h))
            
            # Remove overlapping regions
            regions = self._non_max_suppression(regions, 0.3)
            
            return regions
            
        except Exception as e:
            logger.error(f"Region finding failed: {e}")
            return []
    
    def _non_max_suppression(self, regions: List[Tuple[int, int, int, int]], 
                           overlap_thresh: float) -> List[Tuple[int, int, int, int]]:
        """Apply non-maximum suppression to remove overlapping regions."""
        if len(regions) == 0:
            return []
        
        # Convert to numpy array for easier manipulation
        boxes = np.array(regions)
        
        # Initialize the list of picked indexes
        pick = []
        
        # Grab the coordinates of the bounding boxes
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 0] + boxes[:, 2]
        y2 = boxes[:, 1] + boxes[:, 3]
        
        # Compute the area of the bounding boxes and sort by the bottom-right y-coordinate
        area = (x2 - x1 + 1) * (y2 - y1 + 1)
        idxs = np.argsort(y2)
        
        # Keep looping while some indexes still remain in the indexes list
        while len(idxs) > 0:
            # Grab the last index in the indexes list and add the index value to the list of picked indexes
            last = len(idxs) - 1
            i = idxs[last]
            pick.append(i)
            
            # Find the largest (x, y) coordinates for the start of the bounding box
            # and the smallest (x, y) coordinates for the end of the bounding box
            xx1 = np.maximum(x1[i], x1[idxs[:last]])
            yy1 = np.maximum(y1[i], y1[idxs[:last]])
            xx2 = np.minimum(x2[i], x2[idxs[:last]])
            yy2 = np.minimum(y2[i], y2[idxs[:last]])
            
            # Compute the width and height of the bounding box
            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)
            
            # Compute the ratio of overlap
            overlap = (w * h) / area[idxs[:last]]
            
            # Delete all indexes from the index list that have overlap greater than threshold
            idxs = np.delete(idxs, np.concatenate(([last],
                np.where(overlap > overlap_thresh)[0])))
        
        # Return only the bounding boxes that were picked
        return [tuple(boxes[i]) for i in pick]
    
    def _map_prompt_to_element_type(self, prompt: str) -> ElementType:
        """Map CLIP text prompt to element type."""
        prompt_lower = prompt.lower()
        
        if "button" in prompt_lower:
            return ElementType.BUTTON
        elif "link" in prompt_lower or "hyperlink" in prompt_lower:
            return ElementType.LINK
        elif "input" in prompt_lower or "text" in prompt_lower or "search" in prompt_lower:
            return ElementType.INPUT
        elif "dropdown" in prompt_lower or "menu" in prompt_lower:
            return ElementType.DROPDOWN
        elif "checkbox" in prompt_lower:
            return ElementType.CHECKBOX
        elif "radio" in prompt_lower:
            return ElementType.RADIO
        elif "slider" in prompt_lower:
            return ElementType.SLIDER
        elif "toggle" in prompt_lower or "switch" in prompt_lower:
            return ElementType.TOGGLE
        elif "tab" in prompt_lower:
            return ElementType.TAB
        elif "icon" in prompt_lower:
            return ElementType.ICON_BUTTON
        elif "image" in prompt_lower and "click" in prompt_lower:
            return ElementType.IMAGE_BUTTON
        else:
            return ElementType.CUSTOM_INTERACTIVE
    
    def _map_yolo_class_to_element(self, class_id: int) -> Optional[ElementType]:
        """Map YOLO class ID to element type."""
        # COCO class mapping (simplified)
        # In production, you'd want a more comprehensive mapping
        coco_classes = {
            67: ElementType.DROPDOWN,  # cell phone (often used as menu)
            73: ElementType.BUTTON,    # book (could be a button)
            74: ElementType.BUTTON,    # clock (could be a button)
            75: ElementType.BUTTON,    # scissors (could be a button)
            76: ElementType.BUTTON,    # teddy bear (could be a button)
            77: ElementType.BUTTON,    # hair drier (could be a button)
            78: ElementType.BUTTON,    # toothbrush (could be a button)
        }
        return coco_classes.get(class_id, ElementType.CUSTOM_INTERACTIVE)
    
    def _classify_by_heuristics(self, width: int, height: int, 
                              aspect_ratio: float, region: np.ndarray) -> Optional[ElementType]:
        """Classify element type based on heuristics."""
        # Buttons are usually wider than tall
        if 1.5 < aspect_ratio < 5 and 30 < height < 80:
            return ElementType.BUTTON
        
        # Inputs are usually rectangular with moderate aspect ratio
        elif 2 < aspect_ratio < 10 and 25 < height < 60:
            return ElementType.INPUT
        
        # Checkboxes are usually square
        elif 0.8 < aspect_ratio < 1.2 and 15 < width < 30:
            return ElementType.CHECKBOX
        
        # Links are usually text-like (wider, shorter)
        elif aspect_ratio > 3 and height < 30:
            return ElementType.LINK
        
        # Default to custom interactive
        else:
            return ElementType.CUSTOM_INTERACTIVE


class LayoutAnalyzer:
    """
    Analyzes webpage layout using visual AI to detect interactive elements.
    
    Combines computer vision with DOM analysis for hybrid element detection,
    eliminating the need for brittle CSS/XPath selectors.
    """
    
    def __init__(self, 
                 visual_model_name: str = "clip",
                 confidence_threshold: float = 0.5,
                 enable_hybrid_detection: bool = True,
                 cache_results: bool = True):
        """
        Initialize the LayoutAnalyzer.
        
        Args:
            visual_model_name: Name of the visual model to use ('clip', 'florence', 'yolo')
            confidence_threshold: Minimum confidence for element detection (0.0 to 1.0)
            enable_hybrid_detection: Whether to combine visual and DOM detection
            cache_results: Whether to cache analysis results
        """
        self.visual_model = VisualModelInterface(visual_model_name)
        self.confidence_threshold = confidence_threshold
        self.enable_hybrid_detection = enable_hybrid_detection
        self.cache_results = cache_results
        self._cache = {}
        self._initialized = False
        
        # Element type priorities for interaction
        self.element_priorities = {
            ElementType.BUTTON: 10,
            ElementType.LINK: 9,
            ElementType.INPUT: 8,
            ElementType.SELECT: 7,
            ElementType.TEXTAREA: 6,
            ElementType.CHECKBOX: 5,
            ElementType.RADIO: 5,
            ElementType.TOGGLE: 4,
            ElementType.SLIDER: 3,
            ElementType.DROPDOWN: 2,
            ElementType.TAB: 1,
            ElementType.ICON_BUTTON: 0,
            ElementType.IMAGE_BUTTON: -1,
            ElementType.MENU_ITEM: -2,
            ElementType.CUSTOM_INTERACTIVE: -3
        }
    
    async def initialize(self):
        """Initialize the visual model and other resources."""
        if not self._initialized:
            await self.visual_model.initialize()
            self._initialized = True
            logger.info("LayoutAnalyzer initialized successfully")
    
    async def analyze_layout(self, page: Page) -> LayoutAnalysisResult:
        """
        Analyze the current page layout to detect interactive elements.
        
        Args:
            page: The Page object to analyze
            
        Returns:
            LayoutAnalysisResult containing detected elements and metadata
        """
        import time
        start_time = time.time()
        
        if not self._initialized:
            await self.initialize()
        
        # Check cache
        cache_key = await self._generate_cache_key(page)
        if self.cache_results and cache_key in self._cache:
            logger.debug("Returning cached layout analysis")
            return self._cache[cache_key]
        
        try:
            # Take screenshot for visual analysis
            screenshot = await page.screenshot()
            image = Image.open(io.BytesIO(screenshot))
            
            # Get page dimensions
            page_width = await page.evaluate("document.documentElement.scrollWidth")
            page_height = await page.evaluate("document.documentElement.scrollHeight")
            viewport_width = await page.evaluate("window.innerWidth")
            viewport_height = await page.evaluate("window.innerHeight")
            scroll_x = await page.evaluate("window.scrollX")
            scroll_y = await page.evaluate("window.scrollY")
            
            # Visual detection
            visual_elements = await self._detect_visual_elements(image)
            
            # DOM detection
            dom_elements = await self._detect_dom_elements(page)
            
            # Hybrid detection (combine visual and DOM)
            if self.enable_hybrid_detection:
                hybrid_elements = await self._hybrid_detection(visual_elements, dom_elements, page)
            else:
                hybrid_elements = visual_elements
            
            # Filter by confidence
            filtered_elements = [
                elem for elem in hybrid_elements 
                if elem.confidence_score >= self.confidence_threshold
            ]
            
            # Sort by priority and confidence
            sorted_elements = sorted(
                filtered_elements,
                key=lambda x: (
                    self.element_priorities.get(x.element_type, 0),
                    x.confidence_score
                ),
                reverse=True
            )
            
            analysis_time = (time.time() - start_time) * 1000
            
            result = LayoutAnalysisResult(
                elements=sorted_elements,
                page_width=page_width,
                page_height=page_height,
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                scroll_position=(scroll_x, scroll_y),
                analysis_time_ms=analysis_time,
                dom_elements_analyzed=len(dom_elements),
                visual_elements_detected=len(visual_elements),
                hybrid_matches=len(hybrid_elements) - len(visual_elements),
                metadata={
                    "visual_model": self.visual_model.model_name,
                    "confidence_threshold": self.confidence_threshold,
                    "hybrid_enabled": self.enable_hybrid_detection
                }
            )
            
            # Cache result
            if self.cache_results:
                self._cache[cache_key] = result
            
            logger.info(f"Layout analysis completed: {len(sorted_elements)} elements detected in {analysis_time:.2f}ms")
            
            return result
            
        except Exception as e:
            logger.error(f"Layout analysis failed: {e}")
            # Return empty result on failure
            return LayoutAnalysisResult(
                elements=[],
                page_width=0,
                page_height=0,
                viewport_width=0,
                viewport_height=0,
                scroll_position=(0, 0),
                analysis_time_ms=0,
                dom_elements_analyzed=0,
                visual_elements_detected=0,
                hybrid_matches=0,
                metadata={"error": str(e)}
            )
    
    async def _detect_visual_elements(self, image: Image.Image) -> List[VisualElement]:
        """Detect elements using visual AI."""
        try:
            raw_detections = await self.visual_model.detect_elements(image)
            
            visual_elements = []
            for detection in raw_detections:
                element = VisualElement(
                    element_type=detection["element_type"],
                    bounding_box=detection["bounding_box"],
                    confidence=self._score_to_confidence(detection["confidence"]),
                    confidence_score=detection["confidence"],
                    text_content=detection.get("text"),
                    visual_features=detection.get("features", {}),
                    is_shadow_dom=False,  # Visual detection doesn't know about DOM structure
                    is_dynamically_loaded=False
                )
                visual_elements.append(element)
            
            return visual_elements
            
        except Exception as e:
            logger.error(f"Visual detection failed: {e}")
            return []
    
    async def _detect_dom_elements(self, page: Page) -> List[VisualElement]:
        """Detect interactive elements from DOM."""
        try:
            # JavaScript to find interactive elements in DOM
            js_code = """
            () => {
                const interactiveSelectors = [
                    'button', 'a[href]', 'input', 'select', 'textarea',
                    '[role="button"]', '[role="link"]', '[role="checkbox"]',
                    '[role="radio"]', '[role="tab"]', '[role="menuitem"]',
                    '[onclick]', '[tabindex]', '[contenteditable]',
                    'div[onclick]', 'span[onclick]', 'li[onclick]'
                ];
                
                const elements = [];
                const seen = new Set();
                
                interactiveSelectors.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => {
                        if (!seen.has(el)) {
                            seen.add(el);
                            
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            
                            // Skip hidden elements
                            if (style.display === 'none' || 
                                style.visibility === 'hidden' ||
                                style.opacity === '0' ||
                                rect.width === 0 || 
                                rect.height === 0) {
                                return;
                            }
                            
                            // Check if in shadow DOM
                            let inShadow = false;
                            let parent = el;
                            while (parent) {
                                if (parent.getRootNode() instanceof ShadowRoot) {
                                    inShadow = true;
                                    break;
                                }
                                parent = parent.parentElement;
                            }
                            
                            elements.push({
                                tagName: el.tagName.toLowerCase(),
                                type: el.type || '',
                                role: el.getAttribute('role') || '',
                                text: el.textContent?.trim()?.substring(0, 100) || '',
                                boundingBox: {
                                    x: Math.round(rect.left + window.scrollX),
                                    y: Math.round(rect.top + window.scrollY),
                                    width: Math.round(rect.width),
                                    height: Math.round(rect.height)
                                },
                                isShadowDom: inShadow,
                                selector: el.id ? `#${el.id}` : 
                                        el.className ? `.${el.className.split(' ')[0]}` : el.tagName.toLowerCase(),
                                attributes: {
                                    href: el.getAttribute('href'),
                                    placeholder: el.getAttribute('placeholder'),
                                    value: el.value,
                                    disabled: el.disabled,
                                    readonly: el.readOnly
                                }
                            });
                        }
                    });
                });
                
                return elements;
            }
            """
            
            dom_elements_data = await page.evaluate(js_code)
            
            visual_elements = []
            for data in dom_elements_data:
                # Map DOM element type to our ElementType
                element_type = self._map_dom_to_element_type(
                    data["tagName"],
                    data["type"],
                    data["role"]
                )
                
                if element_type:
                    element = VisualElement(
                        element_type=element_type,
                        bounding_box=(
                            data["boundingBox"]["x"],
                            data["boundingBox"]["y"],
                            data["boundingBox"]["width"],
                            data["boundingBox"]["height"]
                        ),
                        confidence=DetectionConfidence.HIGH,
                        confidence_score=0.9,  # High confidence for DOM elements
                        text_content=data["text"],
                        selector=data["selector"],
                        is_shadow_dom=data["isShadowDom"],
                        is_dynamically_loaded=False,  # Would need mutation observer to detect
                        accessibility_label=data["attributes"].get("aria-label")
                    )
                    visual_elements.append(element)
            
            return visual_elements
            
        except Exception as e:
            logger.error(f"DOM detection failed: {e}")
            return []
    
    async def _hybrid_detection(self, 
                              visual_elements: List[VisualElement],
                              dom_elements: List[VisualElement],
                              page: Page) -> List[VisualElement]:
        """
        Combine visual and DOM detection for more accurate results.
        
        Matches visual detections with DOM elements and enhances with DOM information.
        """
        try:
            hybrid_elements = []
            matched_dom_indices = set()
            
            # Try to match visual elements with DOM elements
            for visual_elem in visual_elements:
                best_match = None
                best_iou = 0
                
                for i, dom_elem in enumerate(dom_elements):
                    if i in matched_dom_indices:
                        continue
                    
                    # Calculate Intersection over Union
                    iou = self._calculate_iou(
                        visual_elem.bounding_box,
                        dom_elem.bounding_box
                    )
                    
                    if iou > 0.3 and iou > best_iou:  # 30% overlap threshold
                        best_iou = iou
                        best_match = (i, dom_elem)
                
                if best_match:
                    idx, dom_elem = best_match
                    matched_dom_indices.add(idx)
                    
                    # Create hybrid element combining visual and DOM information
                    hybrid_elem = VisualElement(
                        element_type=visual_elem.element_type,  # Trust visual classification
                        bounding_box=dom_elem.bounding_box,  # Use DOM bounding box (more accurate)
                        confidence=DetectionConfidence.HIGH,
                        confidence_score=max(visual_elem.confidence_score, dom_elem.confidence_score),
                        text_content=dom_elem.text_content or visual_elem.text_content,
                        visual_features=visual_elem.visual_features,
                        dom_element=None,  # Would need to store actual DOM reference
                        selector=dom_elem.selector,
                        is_shadow_dom=dom_elem.is_shadow_dom,
                        is_dynamically_loaded=visual_elem.is_dynamically_loaded,
                        accessibility_label=dom_elem.accessibility_label
                    )
                    hybrid_elements.append(hybrid_elem)
                else:
                    # Visual element not found in DOM - might be shadow DOM or dynamic
                    visual_elem.is_dynamically_loaded = True
                    visual_elem.confidence = DetectionConfidence.MEDIUM
                    visual_elem.confidence_score *= 0.8  # Reduce confidence
                    hybrid_elements.append(visual_elem)
            
            # Add unmatched DOM elements
            for i, dom_elem in enumerate(dom_elements):
                if i not in matched_dom_indices:
                    dom_elem.confidence = DetectionConfidence.HIGH
                    hybrid_elements.append(dom_elem)
            
            return hybrid_elements
            
        except Exception as e:
            logger.error(f"Hybrid detection failed: {e}")
            return visual_elements + dom_elements
    
    def _calculate_iou(self, box1: Tuple[int, int, int, int], 
                      box2: Tuple[int, int, int, int]) -> float:
        """Calculate Intersection over Union between two bounding boxes."""
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        # Calculate coordinates of intersection
        xi1 = max(x1, x2)
        yi1 = max(y1, y2)
        xi2 = min(x1 + w1, x2 + w2)
        yi2 = min(y1 + h1, y2 + h2)
        
        # Calculate area of intersection
        inter_width = max(0, xi2 - xi1)
        inter_height = max(0, yi2 - yi1)
        inter_area = inter_width * inter_height
        
        # Calculate areas of both boxes
        box1_area = w1 * h1
        box2_area = w2 * h2
        
        # Calculate IoU
        union_area = box1_area + box2_area - inter_area
        
        if union_area == 0:
            return 0.0
        
        return inter_area / union_area
    
    def _map_dom_to_element_type(self, 
                               tag_name: str, 
                               input_type: str, 
                               role: str) -> Optional[ElementType]:
        """Map DOM element properties to ElementType."""
        tag_name = tag_name.lower()
        input_type = input_type.lower() if input_type else ""
        role = role.lower() if role else ""
        
        # Map by ARIA role first
        role_mapping = {
            "button": ElementType.BUTTON,
            "link": ElementType.LINK,
            "checkbox": ElementType.CHECKBOX,
            "radio": ElementType.RADIO,
            "tab": ElementType.TAB,
            "menuitem": ElementType.MENU_ITEM,
            "slider": ElementType.SLIDER,
            "switch": ElementType.TOGGLE
        }
        
        if role in role_mapping:
            return role_mapping[role]
        
        # Map by tag name
        if tag_name == "button":
            return ElementType.BUTTON
        elif tag_name == "a":
            return ElementType.LINK
        elif tag_name == "input":
            input_mapping = {
                "text": ElementType.INPUT,
                "password": ElementType.INPUT,
                "email": ElementType.INPUT,
                "number": ElementType.INPUT,
                "search": ElementType.INPUT,
                "tel": ElementType.INPUT,
                "url": ElementType.INPUT,
                "checkbox": ElementType.CHECKBOX,
                "radio": ElementType.RADIO,
                "range": ElementType.SLIDER,
                "submit": ElementType.BUTTON,
                "button": ElementType.BUTTON,
                "reset": ElementType.BUTTON,
                "image": ElementType.IMAGE_BUTTON
            }
            return input_mapping.get(input_type, ElementType.INPUT)
        elif tag_name == "select":
            return ElementType.SELECT
        elif tag_name == "textarea":
            return ElementType.TEXTAREA
        
        # Check for onclick handler or tabindex
        return ElementType.CUSTOM_INTERACTIVE
    
    def _score_to_confidence(self, score: float) -> DetectionConfidence:
        """Convert numerical score to confidence level."""
        if score >= 0.8:
            return DetectionConfidence.HIGH
        elif score >= 0.6:
            return DetectionConfidence.MEDIUM
        elif score >= 0.4:
            return DetectionConfidence.LOW
        else:
            return DetectionConfidence.UNCERTAIN
    
    async def _generate_cache_key(self, page: Page) -> str:
        """Generate a cache key based on page state."""
        try:
            url = page.url
            # Get a hash of the page content for caching
            content_hash = await page.evaluate("""
                () => {
                    const content = document.documentElement.outerHTML;
                    let hash = 0;
                    for (let i = 0; i < content.length; i++) {
                        const char = content.charCodeAt(i);
                        hash = ((hash << 5) - hash) + char;
                        hash = hash & hash;
                    }
                    return hash.toString(16);
                }
            """)
            return f"{url}:{content_hash}"
        except:
            return f"unknown:{id(page)}"
    
    def clear_cache(self):
        """Clear the analysis cache."""
        self._cache.clear()
        logger.debug("Layout analysis cache cleared")
    
    async def find_element_by_description(self, 
                                        page: Page, 
                                        description: str,
                                        element_type: Optional[ElementType] = None) -> Optional[VisualElement]:
        """
        Find an element by natural language description.
        
        Args:
            page: The Page object to search
            description: Natural language description (e.g., "blue login button")
            element_type: Optional filter by element type
            
        Returns:
            The best matching VisualElement or None
        """
        try:
            # Analyze layout
            analysis = await self.analyze_layout(page)
            
            # If we have a visual model, use it for semantic matching
            if self.visual_model.model_name in ["clip", "florence"]:
                return await self._semantic_element_search(page, description, element_type)
            
            # Fallback to text-based matching
            best_match = None
            best_score = 0
            
            for element in analysis.elements:
                if element_type and element.element_type != element_type:
                    continue
                
                score = 0
                
                # Text matching
                if element.text_content:
                    # Simple keyword matching
                    desc_words = description.lower().split()
                    text_words = element.text_content.lower().split()
                    
                    common_words = set(desc_words) & set(text_words)
                    score += len(common_words) * 10
                
                # Type matching
                if element_type and element.element_type == element_type:
                    score += 5
                
                # Confidence bonus
                score += element.confidence_score * 2
                
                if score > best_score:
                    best_score = score
                    best_match = element
            
            return best_match if best_score > 0 else None
            
        except Exception as e:
            logger.error(f"Element search failed: {e}")
            return None
    
    async def _semantic_element_search(self, 
                                     page: Page, 
                                     description: str,
                                     element_type: Optional[ElementType] = None) -> Optional[VisualElement]:
        """Use visual model for semantic element search."""
        try:
            # Take screenshot
            screenshot = await page.screenshot()
            image = Image.open(io.BytesIO(screenshot))
            
            # Get all elements
            analysis = await self.analyze_layout(page)
            
            # For each element, crop the region and compare with description
            best_match = None
            best_similarity = 0
            
            for element in analysis.elements:
                if element_type and element.element_type != element_type:
                    continue
                
                # Crop element region
                x, y, w, h = element.bounding_box
                element_image = image.crop((x, y, x + w, y + h))
                
                # Calculate similarity with description
                similarity = await self._calculate_visual_similarity(
                    element_image, 
                    description
                )
                
                if similarity > best_similarity and similarity > 0.3:
                    best_similarity = similarity
                    best_match = element
            
            return best_match
            
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return None
    
    async def _calculate_visual_similarity(self, image: Image.Image, text: str) -> float:
        """Calculate similarity between an image and text description."""
        try:
            if self.visual_model.model_name == "clip":
                import torch
                import clip
                
                # Preprocess
                image_input = self.visual_model.preprocessor(image).unsqueeze(0).to(self.visual_model.device)
                text_input = clip.tokenize([text]).to(self.visual_model.device)
                
                with torch.no_grad():
                    image_features = self.visual_model.model.encode_image(image_input)
                    text_features = self.visual_model.model.encode_text(text_input)
                    
                    # Normalize
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                    text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                    
                    # Calculate similarity
                    similarity = (image_features @ text_features.T).item()
                    
                    return max(0, similarity)  # CLIP can give negative similarities
            
            return 0.0
            
        except Exception as e:
            logger.error(f"Similarity calculation failed: {e}")
            return 0.0


# Integration with existing veil modules
class EnhancedElement(Element):
    """Extended Element class with visual detection capabilities."""
    
    def __init__(self, *args, visual_element: Optional[VisualElement] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.visual_element = visual_element
        self.detection_method = "dom" if visual_element is None else "hybrid"
        self.visual_confidence = visual_element.confidence_score if visual_element else 0.0


# Factory function for easy integration
async def create_layout_analyzer(config: Optional[Dict[str, Any]] = None) -> LayoutAnalyzer:
    """
    Factory function to create and initialize a LayoutAnalyzer.
    
    Args:
        config: Optional configuration dictionary
        
    Returns:
        Initialized LayoutAnalyzer instance
    """
    config = config or {}
    
    analyzer = LayoutAnalyzer(
        visual_model_name=config.get("visual_model", "clip"),
        confidence_threshold=config.get("confidence_threshold", 0.5),
        enable_hybrid_detection=config.get("hybrid_detection", True),
        cache_results=config.get("cache_results", True)
    )
    
    await analyzer.initialize()
    return analyzer


# Example usage and testing
if __name__ == "__main__":
    import asyncio
    from veil.actor.page import Page
    
    async def test_layout_analyzer():
        """Test the LayoutAnalyzer with a sample page."""
        # This would typically be called from the main browser automation code
        analyzer = await create_layout_analyzer({
            "visual_model": "clip",
            "confidence_threshold": 0.6
        })
        
        # In real usage, you'd pass an actual Page object
        # page = Page(...)
        # result = await analyzer.analyze_layout(page)
        # print(f"Found {len(result.elements)} interactive elements")
        
        print("LayoutAnalyzer module loaded successfully")
    
    # Run test if executed directly
    asyncio.run(test_layout_analyzer())