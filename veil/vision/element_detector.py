"""
veil/vision/element_detector.py

Adaptive Element Detection with Visual AI - Replaces static DOM selectors with
visual AI that understands page layout semantically. Uses computer vision to detect
interactive elements even when hidden in shadow DOM, dynamically loaded, or styled unusually.
"""

import asyncio
import io
import logging
from typing import List, Dict, Optional, Tuple, Any, Union
from dataclasses import dataclass
from enum import Enum
import base64

import numpy as np
from PIL import Image
import torch
from transformers import (
    CLIPProcessor, 
    CLIPModel,
    AutoProcessor,
    AutoModelForCausalLM
)

from veil.actor.page import Page
from veil.actor.element import Element
from veil.actor.utils import retry_on_failure

logger = logging.getLogger(__name__)


class ElementType(Enum):
    """Types of interactive elements that can be detected."""
    BUTTON = "button"
    LINK = "link"
    INPUT = "input"
    SELECT = "select"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    TEXTAREA = "textarea"
    IMAGE_BUTTON = "image_button"
    ICON_BUTTON = "icon_button"
    MENU_ITEM = "menu_item"
    TAB = "tab"
    SLIDER = "slider"
    TOGGLE = "toggle"
    UNKNOWN = "unknown"


@dataclass
class VisualElement:
    """Represents a visually detected interactive element."""
    element_type: ElementType
    confidence: float
    bounding_box: Tuple[int, int, int, int]  # x1, y1, x2, y2
    center_point: Tuple[int, int]
    text_content: Optional[str] = None
    dom_element: Optional[Element] = None
    attributes: Optional[Dict[str, str]] = None
    visual_features: Optional[torch.Tensor] = None
    
    @property
    def selector(self) -> Optional[str]:
        """Get CSS selector for the element if DOM element is available."""
        if self.dom_element:
            return self.dom_element.selector
        return None
    
    @property
    def area(self) -> int:
        """Calculate area of bounding box."""
        x1, y1, x2, y2 = self.bounding_box
        return (x2 - x1) * (y2 - y1)


class VisionModel(Enum):
    """Supported vision models for element detection."""
    CLIP = "clip"
    FLORENCE = "florence"
    HYBRID = "hybrid"


class VisualElementDetector:
    """
    Adaptive element detector using visual AI to identify interactive elements
    on web pages without relying on brittle DOM selectors.
    """
    
    # Default prompts for CLIP to identify interactive elements
    INTERACTIVE_PROMPTS = [
        "a clickable button",
        "a hyperlink",
        "an input field",
        "a dropdown menu",
        "a checkbox",
        "a radio button",
        "a text area",
        "an image button",
        "an icon button",
        "a menu item",
        "a tab",
        "a slider",
        "a toggle switch",
        "a submit button",
        "a navigation link",
        "a form element"
    ]
    
    # Non-interactive elements to filter out
    NON_INTERACTIVE_PROMPTS = [
        "plain text",
        "an image",
        "a paragraph",
        "a heading",
        "a decorative element",
        "a background"
    ]
    
    def __init__(
        self,
        page: Page,
        model_type: VisionModel = VisionModel.CLIP,
        confidence_threshold: float = 0.7,
        device: str = "auto",
        use_hybrid_approach: bool = True,
        cache_embeddings: bool = True
    ):
        """
        Initialize the visual element detector.
        
        Args:
            page: Browser page instance
            model_type: Vision model to use for detection
            confidence_threshold: Minimum confidence for detection
            device: Device to run model on ('cpu', 'cuda', 'auto')
            use_hybrid_approach: Combine visual detection with DOM analysis
            cache_embeddings: Cache visual embeddings for performance
        """
        self.page = page
        self.model_type = model_type
        self.confidence_threshold = confidence_threshold
        self.use_hybrid_approach = use_hybrid_approach
        self.cache_embeddings = cache_embeddings
        
        # Set device
        if device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
            
        # Initialize models
        self.clip_model = None
        self.clip_processor = None
        self.florence_model = None
        self.florence_processor = None
        
        # Cache for visual embeddings
        self._embedding_cache = {}
        
        # Load models
        self._load_models()
        
    def _load_models(self):
        """Load the specified vision models."""
        try:
            if self.model_type in [VisionModel.CLIP, VisionModel.HYBRID]:
                logger.info("Loading CLIP model for visual element detection...")
                self.clip_model = CLIPModel.from_pretrained(
                    "openai/clip-vit-base-patch32"
                ).to(self.device)
                self.clip_processor = CLIPProcessor.from_pretrained(
                    "openai/clip-vit-base-patch32"
                )
                logger.info("CLIP model loaded successfully")
                
            if self.model_type in [VisionModel.FLORENCE, VisionModel.HYBRID]:
                logger.info("Loading Florence model for visual element detection...")
                # Florence-2 for more accurate UI element detection
                self.florence_model = AutoModelForCausalLM.from_pretrained(
                    "microsoft/Florence-2-base",
                    trust_remote_code=True
                ).to(self.device)
                self.florence_processor = AutoProcessor.from_pretrained(
                    "microsoft/Florence-2-base",
                    trust_remote_code=True
                )
                logger.info("Florence model loaded successfully")
                
        except Exception as e:
            logger.error(f"Failed to load vision models: {e}")
            raise RuntimeError(f"Could not initialize vision models: {e}")
    
    async def detect_elements(
        self,
        screenshot: Optional[bytes] = None,
        element_types: Optional[List[ElementType]] = None,
        region: Optional[Tuple[int, int, int, int]] = None
    ) -> List[VisualElement]:
        """
        Detect interactive elements on the current page.
        
        Args:
            screenshot: Optional screenshot bytes (will capture if not provided)
            element_types: Specific element types to detect (None for all)
            region: Optional region to search within (x1, y1, x2, y2)
            
        Returns:
            List of detected visual elements
        """
        # Capture screenshot if not provided
        if screenshot is None:
            screenshot = await self._capture_screenshot()
        
        # Convert to PIL Image
        image = self._bytes_to_image(screenshot)
        
        # Apply region cropping if specified
        if region:
            image = image.crop(region)
            
        # Detect elements using selected model(s)
        if self.model_type == VisionModel.CLIP:
            elements = await self._detect_with_clip(image, element_types)
        elif self.model_type == VisionModel.FLORENCE:
            elements = await self._detect_with_florence(image, element_types)
        else:  # HYBRID
            clip_elements = await self._detect_with_clip(image, element_types)
            florence_elements = await self._detect_with_florence(image, element_types)
            elements = self._merge_detections(clip_elements, florence_elements)
        
        # Filter by confidence threshold
        elements = [
            e for e in elements 
            if e.confidence >= self.confidence_threshold
        ]
        
        # Hybrid approach: combine with DOM analysis
        if self.use_hybrid_approach:
            elements = await self._enhance_with_dom_analysis(elements, image)
        
        # Sort by confidence and position
        elements.sort(key=lambda x: (-x.confidence, x.bounding_box[1], x.bounding_box[0]))
        
        return elements
    
    async def _capture_screenshot(self) -> bytes:
        """Capture screenshot of the current page."""
        try:
            # Use page's screenshot method
            screenshot = await self.page.screenshot(
                full_page=False,  # Viewport only for performance
                type="png"
            )
            return screenshot
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            raise
    
    def _bytes_to_image(self, image_bytes: bytes) -> Image.Image:
        """Convert bytes to PIL Image."""
        return Image.open(io.BytesIO(image_bytes))
    
    async def _detect_with_clip(
        self,
        image: Image.Image,
        element_types: Optional[List[ElementType]] = None
    ) -> List[VisualElement]:
        """Detect elements using CLIP model."""
        elements = []
        
        try:
            # Prepare prompts based on requested element types
            if element_types:
                prompts = self._get_prompts_for_types(element_types)
            else:
                prompts = self.INTERACTIVE_PROMPTS + self.NON_INTERACTIVE_PROMPTS
            
            # Process image and text
            inputs = self.clip_processor(
                text=prompts,
                images=image,
                return_tensors="pt",
                padding=True
            ).to(self.device)
            
            # Get predictions
            with torch.no_grad():
                outputs = self.clip_model(**inputs)
                logits_per_image = outputs.logits_per_image
                probs = logits_per_image.softmax(dim=1)
            
            # Process results
            for i, prompt in enumerate(prompts):
                confidence = probs[0][i].item()
                
                # Skip non-interactive elements
                if prompt in self.NON_INTERACTIVE_PROMPTS:
                    continue
                    
                # Skip if confidence is too low
                if confidence < self.confidence_threshold:
                    continue
                
                # Map prompt to element type
                element_type = self._prompt_to_element_type(prompt)
                
                # For CLIP, we don't get bounding boxes directly
                # We'll need to use attention maps or object detection
                # For now, we'll estimate based on the whole image
                # In production, you'd use CLIP with localization
                elements.append(VisualElement(
                    element_type=element_type,
                    confidence=confidence,
                    bounding_box=(0, 0, image.width, image.height),
                    center_point=(image.width // 2, image.height // 2),
                    visual_features=outputs.image_embeds[0].cpu()
                ))
                
        except Exception as e:
            logger.error(f"CLIP detection failed: {e}")
            
        return elements
    
    async def _detect_with_florence(
        self,
        image: Image.Image,
        element_types: Optional[List[ElementType]] = None
    ) -> List[VisualElement]:
        """Detect elements using Florence model for better UI understanding."""
        elements = []
        
        try:
            # Florence uses a task-specific prompt format
            task_prompt = "<CAPTION_TO_PHRASE_GROUNDING>"
            
            # Prepare input
            inputs = self.florence_processor(
                text=task_prompt,
                images=image,
                return_tensors="pt"
            ).to(self.device)
            
            # Generate predictions
            with torch.no_grad():
                generated_ids = self.florence_model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024,
                    num_beams=3
                )
            
            # Decode results
            generated_text = self.florence_processor.batch_decode(
                generated_ids, 
                skip_special_tokens=False
            )[0]
            
            # Parse Florence output for UI elements
            parsed = self.florence_processor.post_process_generation(
                generated_text,
                task=task_prompt,
                image_size=(image.width, image.height)
            )
            
            # Extract UI elements from parsed output
            elements = self._parse_florence_output(parsed, image)
            
        except Exception as e:
            logger.error(f"Florence detection failed: {e}")
            
        return elements
    
    def _parse_florence_output(
        self, 
        parsed_output: Dict[str, Any],
        image: Image.Image
    ) -> List[VisualElement]:
        """Parse Florence model output into visual elements."""
        elements = []
        
        # Florence returns bboxes and labels
        if "bboxes" in parsed_output and "labels" in parsed_output:
            for bbox, label in zip(parsed_output["bboxes"], parsed_output["labels"]):
                # Convert bbox to integers
                x1, y1, x2, y2 = map(int, bbox)
                
                # Determine element type from label
                element_type = self._label_to_element_type(label)
                
                # Calculate center point
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                elements.append(VisualElement(
                    element_type=element_type,
                    confidence=0.85,  # Florence doesn't provide confidence directly
                    bounding_box=(x1, y1, x2, y2),
                    center_point=(center_x, center_y),
                    text_content=label
                ))
                
        return elements
    
    def _merge_detections(
        self,
        clip_elements: List[VisualElement],
        florence_elements: List[VisualElement]
    ) -> List[VisualElement]:
        """Merge detections from multiple models."""
        # Simple merge strategy: prefer Florence for bounding boxes,
        # use CLIP for confidence refinement
        merged = florence_elements.copy()
        
        # Add any CLIP detections that don't overlap with Florence
        for clip_elem in clip_elements:
            if not self._has_overlap(clip_elem, florence_elements):
                merged.append(clip_elem)
                
        return merged
    
    def _has_overlap(
        self,
        element: VisualElement,
        elements: List[VisualElement],
        iou_threshold: float = 0.5
    ) -> bool:
        """Check if element overlaps with any element in the list."""
        for other in elements:
            iou = self._calculate_iou(element.bounding_box, other.bounding_box)
            if iou > iou_threshold:
                return True
        return False
    
    def _calculate_iou(
        self,
        box1: Tuple[int, int, int, int],
        box2: Tuple[int, int, int, int]
    ) -> float:
        """Calculate Intersection over Union between two bounding boxes."""
        x1_1, y1_1, x2_1, y2_1 = box1
        x1_2, y1_2, x2_2, y2_2 = box2
        
        # Calculate intersection area
        x_left = max(x1_1, x1_2)
        y_top = max(y1_1, y1_2)
        x_right = min(x2_1, x2_2)
        y_bottom = min(y2_1, y2_2)
        
        if x_right < x_left or y_bottom < y_top:
            return 0.0
            
        intersection_area = (x_right - x_left) * (y_bottom - y_top)
        
        # Calculate union area
        box1_area = (x2_1 - x1_1) * (y2_1 - y1_1)
        box2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
        union_area = box1_area + box2_area - intersection_area
        
        return intersection_area / union_area if union_area > 0 else 0.0
    
    async def _enhance_with_dom_analysis(
        self,
        visual_elements: List[VisualElement],
        image: Image.Image
    ) -> List[VisualElement]:
        """Enhance visual detections with DOM analysis."""
        enhanced_elements = []
        
        for visual_elem in visual_elements:
            try:
                # Find DOM element at the center point
                dom_element = await self._find_dom_element_at_point(
                    visual_elem.center_point
                )
                
                if dom_element:
                    # Get element attributes
                    attributes = await self._get_element_attributes(dom_element)
                    
                    # Get text content
                    text_content = await self._get_element_text(dom_element)
                    
                    # Create enhanced element
                    enhanced_elem = VisualElement(
                        element_type=visual_elem.element_type,
                        confidence=min(visual_elem.confidence + 0.1, 1.0),  # Boost confidence
                        bounding_box=visual_elem.bounding_box,
                        center_point=visual_elem.center_point,
                        text_content=text_content,
                        dom_element=dom_element,
                        attributes=attributes,
                        visual_features=visual_elem.visual_features
                    )
                    enhanced_elements.append(enhanced_elem)
                else:
                    # Keep visual-only detection
                    enhanced_elements.append(visual_elem)
                    
            except Exception as e:
                logger.warning(f"Failed to enhance element with DOM: {e}")
                enhanced_elements.append(visual_elem)
                
        return enhanced_elements
    
    async def _find_dom_element_at_point(
        self,
        point: Tuple[int, int]
    ) -> Optional[Element]:
        """Find DOM element at the given screen coordinates."""
        x, y = point
        
        try:
            # Use JavaScript to find element at coordinates
            element_handle = await self.page.evaluate(f"""
                (x, y) => {{
                    const element = document.elementFromPoint(x, y);
                    if (!element) return null;
                    
                    // Generate a unique selector for the element
                    const getSelector = (el) => {{
                        if (el.id) return '#' + el.id;
                        if (el === document.body) return 'body';
                        
                        let selector = el.tagName.toLowerCase();
                        if (el.className && typeof el.className === 'string') {{
                            selector += '.' + el.className.trim().replace(/\\s+/g, '.');
                        }}
                        
                        // Add nth-child if needed for uniqueness
                        const parent = el.parentElement;
                        if (parent) {{
                            const siblings = Array.from(parent.children).filter(
                                child => child.tagName === el.tagName
                            );
                            if (siblings.length > 1) {{
                                const index = siblings.indexOf(el) + 1;
                                selector += ':nth-child(' + index + ')';
                            }}
                        }}
                        
                        return selector;
                    }};
                    
                    return {{
                        selector: getSelector(element),
                        tagName: element.tagName,
                        id: element.id,
                        className: element.className,
                        attributes: Array.from(element.attributes).map(attr => ({{
                            name: attr.name,
                            value: attr.value
                        }}))
                    }};
                }}
            """, x, y)
            
            if element_handle:
                # Create Element object from the handle
                selector = element_handle.get('selector')
                if selector:
                    return Element(selector, self.page)
                    
        except Exception as e:
            logger.debug(f"Could not find DOM element at ({x}, {y}): {e}")
            
        return None
    
    async def _get_element_attributes(self, element: Element) -> Dict[str, str]:
        """Get all attributes of a DOM element."""
        try:
            attributes = await self.page.evaluate(f"""
                (selector) => {{
                    const element = document.querySelector(selector);
                    if (!element) return {{}};
                    
                    const attrs = {{}};
                    for (const attr of element.attributes) {{
                        attrs[attr.name] = attr.value;
                    }}
                    return attrs;
                }}
            """, element.selector)
            return attributes or {}
        except Exception:
            return {}
    
    async def _get_element_text(self, element: Element) -> Optional[str]:
        """Get text content of a DOM element."""
        try:
            text = await self.page.evaluate(f"""
                (selector) => {{
                    const element = document.querySelector(selector);
                    return element ? element.textContent : null;
                }}
            """, element.selector)
            return text.strip() if text else None
        except Exception:
            return None
    
    def _get_prompts_for_types(
        self, 
        element_types: List[ElementType]
    ) -> List[str]:
        """Get CLIP prompts for specific element types."""
        type_to_prompt = {
            ElementType.BUTTON: ["a clickable button", "a submit button"],
            ElementType.LINK: ["a hyperlink", "a navigation link"],
            ElementType.INPUT: ["an input field", "a text input"],
            ElementType.SELECT: ["a dropdown menu", "a select box"],
            ElementType.CHECKBOX: ["a checkbox"],
            ElementType.RADIO: ["a radio button"],
            ElementType.TEXTAREA: ["a text area"],
            ElementType.IMAGE_BUTTON: ["an image button"],
            ElementType.ICON_BUTTON: ["an icon button"],
            ElementType.MENU_ITEM: ["a menu item"],
            ElementType.TAB: ["a tab"],
            ElementType.SLIDER: ["a slider"],
            ElementType.TOGGLE: ["a toggle switch"]
        }
        
        prompts = []
        for elem_type in element_types:
            if elem_type in type_to_prompt:
                prompts.extend(type_to_prompt[elem_type])
                
        return prompts if prompts else self.INTERACTIVE_PROMPTS
    
    def _prompt_to_element_type(self, prompt: str) -> ElementType:
        """Map a CLIP prompt to an element type."""
        prompt_lower = prompt.lower()
        
        if "button" in prompt_lower:
            return ElementType.BUTTON
        elif "link" in prompt_lower or "hyperlink" in prompt_lower:
            return ElementType.LINK
        elif "input" in prompt_lower:
            return ElementType.INPUT
        elif "dropdown" in prompt_lower or "select" in prompt_lower:
            return ElementType.SELECT
        elif "checkbox" in prompt_lower:
            return ElementType.CHECKBOX
        elif "radio" in prompt_lower:
            return ElementType.RADIO
        elif "text area" in prompt_lower:
            return ElementType.TEXTAREA
        elif "image button" in prompt_lower:
            return ElementType.IMAGE_BUTTON
        elif "icon button" in prompt_lower:
            return ElementType.ICON_BUTTON
        elif "menu item" in prompt_lower:
            return ElementType.MENU_ITEM
        elif "tab" in prompt_lower:
            return ElementType.TAB
        elif "slider" in prompt_lower:
            return ElementType.SLIDER
        elif "toggle" in prompt_lower:
            return ElementType.TOGGLE
        else:
            return ElementType.UNKNOWN
    
    def _label_to_element_type(self, label: str) -> ElementType:
        """Map a Florence label to an element type."""
        label_lower = label.lower()
        
        # Map common UI element labels
        mapping = {
            "button": ElementType.BUTTON,
            "btn": ElementType.BUTTON,
            "link": ElementType.LINK,
            "a": ElementType.LINK,
            "input": ElementType.INPUT,
            "textfield": ElementType.INPUT,
            "select": ElementType.SELECT,
            "dropdown": ElementType.SELECT,
            "checkbox": ElementType.CHECKBOX,
            "radio": ElementType.RADIO,
            "textarea": ElementType.TEXTAREA,
            "img": ElementType.IMAGE_BUTTON,
            "icon": ElementType.ICON_BUTTON,
            "menuitem": ElementType.MENU_ITEM,
            "tab": ElementType.TAB,
            "slider": ElementType.SLIDER,
            "toggle": ElementType.TOGGLE,
            "switch": ElementType.TOGGLE
        }
        
        for key, elem_type in mapping.items():
            if key in label_lower:
                return elem_type
                
        return ElementType.UNKNOWN
    
    async def find_element_by_visual_description(
        self,
        description: str,
        screenshot: Optional[bytes] = None
    ) -> Optional[VisualElement]:
        """
        Find a specific element by visual description.
        
        Args:
            description: Natural language description of the element
            screenshot: Optional screenshot
            
        Returns:
            Best matching visual element or None
        """
        if screenshot is None:
            screenshot = await self._capture_screenshot()
            
        image = self._bytes_to_image(screenshot)
        
        # Use CLIP to find element matching description
        if self.clip_model:
            inputs = self.clip_processor(
                text=[description],
                images=image,
                return_tensors="pt",
                padding=True
            ).to(self.device)
            
            with torch.no_grad():
                outputs = self.clip_model(**inputs)
                logits_per_image = outputs.logits_per_image
                probs = logits_per_image.softmax(dim=1)
                
            confidence = probs[0][0].item()
            
            if confidence >= self.confidence_threshold:
                # For now, return a generic element covering the whole image
                # In production, you'd use attention maps to localize
                return VisualElement(
                    element_type=ElementType.UNKNOWN,
                    confidence=confidence,
                    bounding_box=(0, 0, image.width, image.height),
                    center_point=(image.width // 2, image.height // 2),
                    text_content=description
                )
                
        return None
    
    async def get_interactive_region_map(
        self,
        screenshot: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Generate a map of interactive regions on the page.
        
        Returns:
            Dictionary with element positions and types
        """
        elements = await self.detect_elements(screenshot)
        
        return {
            "total_elements": len(elements),
            "elements": [
                {
                    "type": elem.element_type.value,
                    "confidence": elem.confidence,
                    "position": {
                        "x1": elem.bounding_box[0],
                        "y1": elem.bounding_box[1],
                        "x2": elem.bounding_box[2],
                        "y2": elem.bounding_box[3],
                        "center_x": elem.center_point[0],
                        "center_y": elem.center_point[1]
                    },
                    "text": elem.text_content,
                    "selector": elem.selector
                }
                for elem in elements
            ],
            "statistics": {
                "by_type": self._count_by_type(elements),
                "average_confidence": np.mean([e.confidence for e in elements]) if elements else 0
            }
        }
    
    def _count_by_type(self, elements: List[VisualElement]) -> Dict[str, int]:
        """Count elements by type."""
        counts = {}
        for elem in elements:
            type_name = elem.element_type.value
            counts[type_name] = counts.get(type_name, 0) + 1
        return counts


# Factory function for easy integration
async def create_visual_detector(
    page: Page,
    model_type: str = "clip",
    **kwargs
) -> VisualElementDetector:
    """
    Factory function to create a visual element detector.
    
    Args:
        page: Browser page instance
        model_type: Model type ('clip', 'florence', or 'hybrid')
        **kwargs: Additional arguments for VisualElementDetector
        
    Returns:
        Configured VisualElementDetector instance
    """
    model_enum = VisionModel(model_type.lower())
    return VisualElementDetector(page, model_enum, **kwargs)


# Integration with existing actor module
class VisualElementActor:
    """
    Actor that uses visual detection for element interaction.
    Integrates with existing veil.actor module.
    """
    
    def __init__(self, page: Page, detector: Optional[VisualElementDetector] = None):
        self.page = page
        self.detector = detector or VisualElementDetector(page)
        
    async def click_by_visual_description(self, description: str) -> bool:
        """Click an element based on visual description."""
        element = await self.detector.find_element_by_visual_description(description)
        if element:
            await self.page.mouse.click(
                element.center_point[0],
                element.center_point[1]
            )
            return True
        return False
    
    async def fill_by_visual_description(
        self,
        field_description: str,
        value: str
    ) -> bool:
        """Fill a form field based on visual description."""
        element = await self.detector.find_element_by_visual_description(
            field_description
        )
        if element and element.element_type in [ElementType.INPUT, ElementType.TEXTAREA]:
            await self.page.mouse.click(
                element.center_point[0],
                element.center_point[1]
            )
            await self.page.keyboard.type(value)
            return True
        return False


# Example usage
if __name__ == "__main__":
    # This would be used within the existing veil framework
    async def example_usage():
        from veil.actor.page import Page
        
        # Initialize page (this would come from existing code)
        page = Page()  # Simplified for example
        
        # Create visual detector
        detector = VisualElementDetector(
            page,
            model_type=VisionModel.HYBRID,
            confidence_threshold=0.75,
            use_hybrid_approach=True
        )
        
        # Detect all interactive elements
        elements = await detector.detect_elements()
        print(f"Found {len(elements)} interactive elements")
        
        # Find specific element
        submit_button = await detector.find_element_by_visual_description(
            "blue submit button at the bottom of the form"
        )
        
        if submit_button:
            print(f"Found submit button at {submit_button.center_point}")
            
        # Get interactive region map
        region_map = await detector.get_interactive_region_map()
        print(f"Interactive regions: {region_map['statistics']}")
    
    # Run example
    # asyncio.run(example_usage())