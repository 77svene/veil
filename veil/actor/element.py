"""Element class for element operations with adaptive visual AI detection."""

import asyncio
from typing import TYPE_CHECKING, Literal, Union, Optional, List, Dict, Any
import base64
import io
import json

from cdp_use.client import logger
from typing_extensions import TypedDict

if TYPE_CHECKING:
	from cdp_use.cdp.dom.commands import (
		DescribeNodeParameters,
		FocusParameters,
		GetAttributesParameters,
		GetBoxModelParameters,
		PushNodesByBackendIdsToFrontendParameters,
		RequestChildNodesParameters,
		ResolveNodeParameters,
	)
	from cdp_use.cdp.input.commands import (
		DispatchMouseEventParameters,
	)
	from cdp_use.cdp.input.types import MouseButton
	from cdp_use.cdp.page.commands import CaptureScreenshotParameters
	from cdp_use.cdp.page.types import Viewport
	from cdp_use.cdp.runtime.commands import CallFunctionOnParameters

	from veil.browser.session import BrowserSession

# Type definitions for element operations
ModifierType = Literal['Alt', 'Control', 'Meta', 'Shift']


class Position(TypedDict):
	"""2D position coordinates."""

	x: float
	y: float


class BoundingBox(TypedDict):
	"""Element bounding box with position and dimensions."""

	x: float
	y: float
	width: float
	height: float


class ElementInfo(TypedDict):
	"""Basic information about a DOM element."""

	backendNodeId: int
	nodeId: int | None
	nodeName: str
	nodeType: int
	nodeValue: str | None
	attributes: dict[str, str]
	boundingBox: BoundingBox | None
	error: str | None


class VisualElementInfo(TypedDict):
	"""Visual AI detected element information."""
	
	element_type: str  # button, link, input, etc.
	confidence: float
	boundingBox: BoundingBox
	text_content: str | None
	semantic_role: str | None
	is_interactive: bool
	dom_node_id: int | None


class AdaptiveElementDetector:
	"""Visual AI element detector with hybrid DOM analysis."""
	
	def __init__(self, browser_session: 'BrowserSession'):
		self._browser_session = browser_session
		self._client = browser_session.cdp_client
		self._vision_model = None
		self._initialized = False
		self._cache = {}
		
	async def initialize(self):
		"""Initialize the vision model."""
		if self._initialized:
			return
			
		try:
			# Try to import and initialize vision model
			# Using Florence-2 or similar lightweight model
			from transformers import AutoProcessor, AutoModelForCausalLM
			import torch
			
			model_name = "microsoft/Florence-2-base"
			self._processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
			self._vision_model = AutoModelForCausalLM.from_pretrained(model_name, trust_remote_code=True)
			
			# Move to GPU if available
			if torch.cuda.is_available():
				self._vision_model = self._vision_model.cuda()
				
			self._initialized = True
			logger.info("Visual AI detector initialized with Florence-2 model")
			
		except ImportError:
			logger.warning("Transformers not available, falling back to CLIP")
			try:
				import clip
				self._vision_model, self._preprocess = clip.load("ViT-B/32")
				self._initialized = True
				logger.info("Visual AI detector initialized with CLIP model")
			except ImportError:
				logger.error("No vision models available. Install transformers or clip.")
				self._initialized = False
		except Exception as e:
			logger.error(f"Failed to initialize vision model: {e}")
			self._initialized = False
	
	async def detect_interactive_elements(self, session_id: str | None = None) -> List[VisualElementInfo]:
		"""Detect interactive elements using visual AI and DOM analysis hybrid approach."""
		if not self._initialized:
			await self.initialize()
			
		if not self._initialized:
			return []
		
		# Capture screenshot for visual analysis
		screenshot_data = await self._capture_screenshot(session_id)
		if not screenshot_data:
			return []
		
		# Analyze DOM structure for context
		dom_elements = await self._analyze_dom_structure(session_id)
		
		# Detect elements visually
		visual_elements = await self._detect_visually(screenshot_data)
		
		# Merge visual and DOM detections
		merged_elements = self._merge_detections(visual_elements, dom_elements)
		
		# Filter and validate interactive elements
		validated_elements = self._validate_interactive_elements(merged_elements)
		
		return validated_elements
	
	async def _capture_screenshot(self, session_id: str | None = None) -> bytes | None:
		"""Capture screenshot of current viewport."""
		try:
			params: 'CaptureScreenshotParameters' = {
				'format': 'png',
				'quality': 80,
				'clip': {
					'x': 0,
					'y': 0,
					'width': 0,  # Will be set to viewport
					'height': 0,
					'scale': 1
				}
			}
			
			# Get viewport dimensions
			layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=session_id)
			viewport = layout_metrics['layoutViewport']
			params['clip']['width'] = viewport['clientWidth']
			params['clip']['height'] = viewport['clientHeight']
			
			result = await self._client.send.Page.captureScreenshot(params, session_id=session_id)
			return base64.b64decode(result['data'])
		except Exception as e:
			logger.error(f"Failed to capture screenshot: {e}")
			return None
	
	async def _analyze_dom_structure(self, session_id: str | None = None) -> List[Dict[str, Any]]:
		"""Analyze DOM structure for interactive elements."""
		try:
			# Get document root
			doc_result = await self._client.send.DOM.getDocument(session_id=session_id)
			root_node_id = doc_result['root']['nodeId']
			
			# Query for interactive elements
			interactive_selectors = [
				'button', 'a[href]', 'input', 'select', 'textarea',
				'[role="button"]', '[role="link"]', '[role="checkbox"]',
				'[role="radio"]', '[role="tab"]', '[role="menuitem"]',
				'[onclick]', '[tabindex]', '[contenteditable]'
			]
			
			dom_elements = []
			for selector in interactive_selectors:
				try:
					query_result = await self._client.send.DOM.querySelectorAll(
						params={'nodeId': root_node_id, 'selector': selector},
						session_id=session_id
					)
					
					for node_id in query_result['nodeIds']:
						try:
							# Get element info
							box_model = await self._client.send.DOM.getBoxModel(
								params={'nodeId': node_id},
								session_id=session_id
							)
							
							if 'model' in box_model:
								content = box_model['model']['content']
								if len(content) >= 8:
									dom_elements.append({
										'nodeId': node_id,
										'selector': selector,
										'boundingBox': {
											'x': content[0],
											'y': content[1],
											'width': content[2] - content[0],
											'height': content[5] - content[1]
										}
									})
						except Exception:
							continue
				except Exception:
					continue
			
			return dom_elements
		except Exception as e:
			logger.error(f"Failed to analyze DOM structure: {e}")
			return []
	
	async def _detect_visually(self, screenshot_data: bytes) -> List[VisualElementInfo]:
		"""Detect elements visually using vision model."""
		visual_elements = []
		
		try:
			# Convert screenshot to PIL Image
			from PIL import Image
			import io
			
			image = Image.open(io.BytesIO(screenshot_data))
			
			# Use Florence-2 for element detection
			if hasattr(self, '_processor') and hasattr(self, '_vision_model'):
				# Florence-2 detection
				prompt = "<OD>"  # Object Detection prompt
				inputs = self._processor(text=prompt, images=image, return_tensors="pt")
				
				# Move to same device as model
				if hasattr(self._vision_model, 'device'):
					inputs = {k: v.to(self._vision_model.device) for k, v in inputs.items()}
				
				generated_ids = self._vision_model.generate(
					input_ids=inputs["input_ids"],
					pixel_values=inputs["pixel_values"],
					max_new_tokens=1024,
					num_beams=3,
				)
				
				generated_text = self._processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
				parsed_answer = self._processor.post_process_generation(
					generated_text,
					task="<OD>",
					image_size=(image.width, image.height)
				)
				
				# Parse detected objects
				if '<OD>' in parsed_answer:
					for obj in parsed_answer['<OD>']:
						bbox = obj['bbox']
						label = obj['label']
						confidence = obj.get('confidence', 0.8)
						
						# Map labels to interactive element types
						element_type = self._map_label_to_element_type(label)
						
						if element_type:
							visual_elements.append({
								'element_type': element_type,
								'confidence': confidence,
								'boundingBox': {
									'x': bbox[0],
									'y': bbox[1],
									'width': bbox[2] - bbox[0],
									'height': bbox[3] - bbox[1]
								},
								'text_content': None,
								'semantic_role': label,
								'is_interactive': True,
								'dom_node_id': None
							})
			
			# Fallback to CLIP if Florence not available
			elif hasattr(self, '_vision_model') and hasattr(self, '_preprocess'):
				# CLIP-based detection (simplified)
				# In practice, you'd use a region proposal network first
				visual_elements = await self._detect_with_clip(image)
				
		except Exception as e:
			logger.error(f"Visual detection failed: {e}")
		
		return visual_elements
	
	def _map_label_to_element_type(self, label: str) -> str | None:
		"""Map vision model labels to interactive element types."""
		label_lower = label.lower()
		
		mapping = {
			'button': 'button',
			'link': 'link',
			'a': 'link',
			'input': 'input',
			'textbox': 'input',
			'select': 'select',
			'dropdown': 'select',
			'checkbox': 'checkbox',
			'radio': 'radio',
			'tab': 'tab',
			'menuitem': 'menuitem',
			'img': 'image',
			'image': 'image',
			'icon': 'icon',
		}
		
		for key, value in mapping.items():
			if key in label_lower:
				return value
		
		return None
	
	async def _detect_with_clip(self, image) -> List[VisualElementInfo]:
		"""Detect elements using CLIP model."""
		import torch
		import clip
		from PIL import Image
		import numpy as np
		
		# Predefined prompts for interactive elements
		element_prompts = [
			"a button",
			"a clickable link",
			"a text input field",
			"a dropdown menu",
			"a checkbox",
			"a radio button",
			"a tab",
			"a menu item",
		]
		
		# Simple grid-based detection (in practice, use object detection first)
		visual_elements = []
		grid_size = 100  # pixels
		
		width, height = image.size
		for y in range(0, height, grid_size):
			for x in range(0, width, grid_size):
				# Crop region
				box = (x, y, min(x + grid_size, width), min(y + grid_size, height))
				region = image.crop(box)
				
				# Preprocess for CLIP
				image_input = self._preprocess(region).unsqueeze(0)
				
				# Calculate similarity with prompts
				text_tokens = clip.tokenize(element_prompts)
				
				if torch.cuda.is_available():
					image_input = image_input.cuda()
					text_tokens = text_tokens.cuda()
				
				with torch.no_grad():
					image_features = self._vision_model.encode_image(image_input)
					text_features = self._vision_model.encode_text(text_tokens)
				
				# Calculate similarities
				similarities = (image_features @ text_features.T).softmax(dim=-1)
				similarities = similarities.cpu().numpy()[0]
				
				# Check if any element detected with high confidence
				max_idx = np.argmax(similarities)
				max_sim = similarities[max_idx]
				
				if max_sim > 0.7:  # Threshold
					element_type = element_prompts[max_idx].replace("a ", "").replace(" ", "_")
					visual_elements.append({
						'element_type': element_type,
						'confidence': float(max_sim),
						'boundingBox': {
							'x': x,
							'y': y,
							'width': box[2] - box[0],
							'height': box[3] - box[1]
						},
						'text_content': None,
						'semantic_role': element_type,
						'is_interactive': True,
						'dom_node_id': None
					})
		
		return visual_elements
	
	def _merge_detections(self, visual_elements: List[VisualElementInfo], 
						 dom_elements: List[Dict[str, Any]]) -> List[VisualElementInfo]:
		"""Merge visual and DOM detections using IoU matching."""
		merged = []
		
		# Create spatial index for DOM elements
		dom_by_bbox = {}
		for dom_elem in dom_elements:
			bbox = dom_elem['boundingBox']
			key = self._bbox_to_key(bbox)
			dom_by_bbox[key] = dom_elem
		
		# Match visual elements to DOM elements
		for vis_elem in visual_elements:
			vis_bbox = vis_elem['boundingBox']
			best_match = None
			best_iou = 0.3  # Minimum IoU threshold
			
			# Find closest DOM element by IoU
			for dom_key, dom_elem in dom_by_bbox.items():
				dom_bbox = dom_elem['boundingBox']
				iou = self._calculate_iou(vis_bbox, dom_bbox)
				
				if iou > best_iou:
					best_iou = iou
					best_match = dom_elem
			
			# Update visual element with DOM info if matched
			if best_match:
				vis_elem['dom_node_id'] = best_match['nodeId']
				# Use DOM bounding box for more accuracy
				vis_elem['boundingBox'] = best_match['boundingBox']
				del dom_by_bbox[self._bbox_to_key(best_match['boundingBox'])]
			
			merged.append(vis_elem)
		
		# Add remaining DOM elements that weren't matched
		for dom_elem in dom_by_bbox.values():
			merged.append({
				'element_type': self._infer_element_type_from_selector(dom_elem['selector']),
				'confidence': 0.9,
				'boundingBox': dom_elem['boundingBox'],
				'text_content': None,
				'semantic_role': dom_elem['selector'],
				'is_interactive': True,
				'dom_node_id': dom_elem['nodeId']
			})
		
		return merged
	
	def _bbox_to_key(self, bbox: BoundingBox) -> str:
		"""Create a key for bounding box for indexing."""
		return f"{bbox['x']:.0f}_{bbox['y']:.0f}_{bbox['width']:.0f}_{bbox['height']:.0f}"
	
	def _calculate_iou(self, bbox1: BoundingBox, bbox2: BoundingBox) -> float:
		"""Calculate Intersection over Union between two bounding boxes."""
		# Calculate intersection coordinates
		x1 = max(bbox1['x'], bbox2['x'])
		y1 = max(bbox1['y'], bbox2['y'])
		x2 = min(bbox1['x'] + bbox1['width'], bbox2['x'] + bbox2['width'])
		y2 = min(bbox1['y'] + bbox1['height'], bbox2['y'] + bbox2['height'])
		
		# Calculate intersection area
		intersection_area = max(0, x2 - x1) * max(0, y2 - y1)
		
		# Calculate union area
		area1 = bbox1['width'] * bbox1['height']
		area2 = bbox2['width'] * bbox2['height']
		union_area = area1 + area2 - intersection_area
		
		# Avoid division by zero
		if union_area == 0:
			return 0
		
		return intersection_area / union_area
	
	def _infer_element_type_from_selector(self, selector: str) -> str:
		"""Infer element type from CSS selector."""
		if 'button' in selector:
			return 'button'
		elif 'a' in selector or 'link' in selector:
			return 'link'
		elif 'input' in selector:
			return 'input'
		elif 'select' in selector:
			return 'select'
		elif 'checkbox' in selector:
			return 'checkbox'
		elif 'radio' in selector:
			return 'radio'
		else:
			return 'interactive'
	
	def _validate_interactive_elements(self, elements: List[VisualElementInfo]) -> List[VisualElementInfo]:
		"""Validate and filter interactive elements."""
		validated = []
		
		for elem in elements:
			# Filter out elements that are too small or likely not interactive
			bbox = elem['boundingBox']
			area = bbox['width'] * bbox['height']
			
			if area < 100:  # Too small
				continue
			
			if bbox['width'] < 5 or bbox['height'] < 5:  # Too thin
				continue
			
			# Ensure element has reasonable aspect ratio
			aspect_ratio = bbox['width'] / max(bbox['height'], 1)
			if aspect_ratio > 20 or aspect_ratio < 0.05:  # Unusual aspect ratio
				continue
			
			validated.append(elem)
		
		return validated


class Element:
	"""Element operations using BackendNodeId with adaptive visual detection."""

	def __init__(
		self,
		browser_session: 'BrowserSession',
		backend_node_id: int,
		session_id: str | None = None,
	):
		self._browser_session = browser_session
		self._client = browser_session.cdp_client
		self._backend_node_id = backend_node_id
		self._session_id = session_id
		self._visual_detector = AdaptiveElementDetector(browser_session)
		self._visual_info = None
	
	async def get_visual_info(self) -> VisualElementInfo | None:
		"""Get visual AI information about this element."""
		if not self._visual_info:
			# Try to find this element in visual detections
			visual_elements = await self._visual_detector.detect_interactive_elements(self._session_id)
			
			# Find element by backend node ID or bounding box similarity
			for elem in visual_elements:
				if elem.get('dom_node_id') == self._backend_node_id:
					self._visual_info = elem
					break
			
			# If not found by node ID, try to match by bounding box
			if not self._visual_info:
				dom_bbox = await self._get_bounding_box()
				if dom_bbox:
					for elem in visual_elements:
						iou = self._visual_detector._calculate_iou(dom_bbox, elem['boundingBox'])
						if iou > 0.5:  # Good match
							self._visual_info = elem
							break
		
		return self._visual_info
	
	async def _get_node_id(self) -> int:
		"""Get DOM node ID from backend node ID."""
		params: 'PushNodesByBackendIdsToFrontendParameters' = {'backendNodeIds': [self._backend_node_id]}
		result = await self._client.send.DOM.pushNodesByBackendIdsToFrontend(params, session_id=self._session_id)
		return result['nodeIds'][0]

	async def _get_remote_object_id(self) -> str | None:
		"""Get remote object ID for this element."""
		node_id = await self._get_node_id()
		params: 'ResolveNodeParameters' = {'nodeId': node_id}
		result = await self._client.send.DOM.resolveNode(params, session_id=self._session_id)
		object_id = result['object'].get('objectId', None)

		if not object_id:
			return None
		return object_id
	
	async def _get_bounding_box(self) -> BoundingBox | None:
		"""Get element bounding box using multiple methods."""
		# Try DOM methods first
		try:
			box_model = await self._client.send.DOM.getBoxModel(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'model' in box_model and 'content' in box_model['model']:
				content = box_model['model']['content']
				if len(content) >= 8:
					return {
						'x': content[0],
						'y': content[1],
						'width': content[2] - content[0],
						'height': content[5] - content[1]
					}
		except Exception:
			pass
		
		# Try JavaScript fallback
		try:
			result = await self._client.send.DOM.resolveNode(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				
				bounds_result = await self._client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': """
							function() {
								const rect = this.getBoundingClientRect();
								return {
									x: rect.left,
									y: rect.top,
									width: rect.width,
									height: rect.height
								};
							}
						""",
						'objectId': object_id,
						'returnByValue': True,
					},
					session_id=self._session_id,
				)
				
				if 'result' in bounds_result and 'value' in bounds_result['result']:
					return bounds_result['result']['value']
		except Exception:
			pass
		
		return None

	async def click(
		self,
		button: 'MouseButton' = 'left',
		click_count: int = 1,
		modifiers: list[ModifierType] | None = None,
	) -> None:
		"""Click the element using adaptive visual AI detection with DOM fallback."""

		try:
			# Get viewport dimensions for visibility checks
			layout_metrics = await self._client.send.Page.getLayoutMetrics(session_id=self._session_id)
			viewport_width = layout_metrics['layoutViewport']['clientWidth']
			viewport_height = layout_metrics['layoutViewport']['clientHeight']

			# Try multiple methods to get element geometry
			quads = []

			# Method 1: Try DOM.getContentQuads first (best for inline elements and complex layouts)
			try:
				content_quads_result = await self._client.send.DOM.getContentQuads(
					params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
				)
				if 'quads' in content_quads_result and content_quads_result['quads']:
					quads = content_quads_result['quads']
			except Exception:
				pass

			# Method 2: Fall back to DOM.getBoxModel
			if not quads:
				try:
					box_model = await self._client.send.DOM.getBoxModel(
						params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
					)
					if 'model' in box_model and 'content' in box_model['model']:
						content_quad = box_model['model']['content']
						if len(content_quad) >= 8:
							# Convert box model format to quad format
							quads = [
								[
									content_quad[0],
									content_quad[1],  # x1, y1
									content_quad[2],
									content_quad[3],  # x2, y2
									content_quad[4],
									content_quad[5],  # x3, y3
									content_quad[6],
									content_quad[7],  # x4, y4
								]
							]
				except Exception:
					pass

			# Method 3: Fall back to JavaScript getBoundingClientRect
			if not quads:
				try:
					result = await self._client.send.DOM.resolveNode(
						params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
					)
					if 'object' in result and 'objectId' in result['object']:
						object_id = result['object']['objectId']

						# Get bounding rect via JavaScript
						bounds_result = await self._client.send.Runtime.callFunctionOn(
							params={
								'functionDeclaration': """
									function() {
										const rect = this.getBoundingClientRect();
										return {
											x: rect.left,
											y: rect.top,
											width: rect.width,
											height: rect.height
										};
									}
								""",
								'objectId': object_id,
								'returnByValue': True,
							},
							session_id=self._session_id,
						)

						if 'result' in bounds_result and 'value' in bounds_result['result']:
							rect = bounds_result['result']['value']
							# Convert rect to quad format
							x, y, w, h = rect['x'], rect['y'], rect['width'], rect['height']
							quads = [
								[
									x,
									y,  # top-left
									x + w,
									y,  # top-right
									x + w,
									y + h,  # bottom-right
									x,
									y + h,  # bottom-left
								]
							]
				except Exception:
					pass

			# Method 4: Visual AI detection fallback
			if not quads:
				logger.info("Using visual AI detection for element positioning")
				try:
					visual_info = await self.get_visual_info()
					if visual_info and visual_info.get('boundingBox'):
						bbox = visual_info['boundingBox']
						x, y, w, h = bbox['x'], bbox['y'], bbox['width'], bbox['height']
						quads = [
							[
								x,
								y,  # top-left
								x + w,
								y,  # top-right
								x + w,
								y + h,  # bottom-right
								x,
								y + h,  # bottom-left
							]
						]
						logger.info(f"Visual AI detected element at ({x}, {y}) with size ({w}, {h})")
				except Exception as e:
					logger.warning(f"Visual AI detection failed: {e}")

			# If we still don't have quads, fall back to JS click
			if not quads:
				try:
					result = await self._client.send.DOM.resolveNode(
						params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
					)
					if 'object' not in result or 'objectId' not in result['object']:
						raise Exception('Failed to find DOM element based on backendNodeId, maybe page content changed?')
					object_id = result['object']['objectId']

					await self._client.send.Runtime.callFunctionOn(
						params={
							'functionDeclaration': 'function() { this.click(); }',
							'objectId': object_id,
						},
						session_id=self._session_id,
					)
					await asyncio.sleep(0.05)
					return
				except Exception as js_e:
					raise Exception(f'Failed to click element: {js_e}')

			# Find the largest visible quad within the viewport
			best_quad = None
			best_area = 0

			for quad in quads:
				if len(quad) < 8:
					continue

				# Calculate quad bounds
				xs = [quad[i] for i in range(0, 8, 2)]
				ys = [quad[i] for i in range(1, 8, 2)]
				min_x, max_x = min(xs), max(xs)
				min_y, max_y = min(ys), max(ys)

				# Check if quad intersects with viewport
				if max_x < 0 or max_y < 0 or min_x > viewport_width or min_y > viewport_height:
					continue  # Quad is completely outside viewport

				# Calculate visible area (intersection with viewport)
				visible_min_x = max(0, min_x)
				visible_max_x = min(viewport_width, max_x)
				visible_min_y = max(0, min_y)
				visible_max_y = min(viewport_height, max_y)

				visible_width = visible_max_x - visible_min_x
				visible_height = visible_max_y - visible_min_y
				visible_area = visible_width * visible_height

				if visible_area > best_area:
					best_area = visible_area
					best_quad = quad

			if not best_quad:
				# No visible quad found, use the first quad anyway
				best_quad = quads[0]

			# Calculate center point of the best quad
			center_x = sum(best_quad[i] for i in range(0, 8, 2)) / 4
			center_y = sum(best_quad[i] for i in range(1, 8, 2)) / 4

			# Ensure click point is within viewport bounds
			center_x = max(0, min(viewport_width - 1, center_x))
			center_y = max(0, min(viewport_height - 1, center_y))

			# Scroll element into view
			try:
				await self._client.send.DOM.scrollIntoViewIfNeeded(
					params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
				)
				await asyncio.sleep(0.05)  # Wait for scroll to complete
			except Exception:
				pass

			# Dispatch mouse events
			# Move to element
			move_params: 'DispatchMouseEventParameters' = {
				'type': 'mouseMoved',
				'x': center_x,
				'y': center_y,
			}
			await self._client.send.Input.dispatchMouseEvent(move_params, session_id=self._session_id)

			# Mouse down
			down_params: 'DispatchMouseEventParameters' = {
				'type': 'mousePressed',
				'x': center_x,
				'y': center_y,
				'button': button,
				'clickCount': click_count,
			}
			if modifiers:
				down_params['modifiers'] = sum(1 << i for i, m in enumerate(modifiers))
			await self._client.send.Input.dispatchMouseEvent(down_params, session_id=self._session_id)

			# Mouse up
			up_params: 'DispatchMouseEventParameters' = {
				'type': 'mouseReleased',
				'x': center_x,
				'y': center_y,
				'button': button,
				'clickCount': click_count,
			}
			await self._client.send.Input.dispatchMouseEvent(up_params, session_id=self._session_id)

			await asyncio.sleep(0.05)  # Small delay after click

		except Exception as e:
			logger.error(f"Error clicking element: {e}")
			raise

	async def type_text(self, text: str, delay: float = 0.05) -> None:
		"""Type text into the element using visual AI positioning if needed."""
		try:
			# First click to focus the element
			await self.click()
			await asyncio.sleep(0.1)
			
			# Type each character
			for char in text:
				# Dispatch key events for the character
				key_params = {
					'type': 'keyDown',
					'text': char,
				}
				await self._client.send.Input.dispatchKeyEvent(key_params, session_id=self._session_id)
				
				key_params['type'] = 'keyUp'
				await self._client.send.Input.dispatchKeyEvent(key_params, session_id=self._session_id)
				
				await asyncio.sleep(delay)
				
		except Exception as e:
			logger.error(f"Error typing text: {e}")
			raise

	async def get_text(self) -> str | None:
		"""Get text content of the element."""
		try:
			result = await self._client.send.DOM.resolveNode(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				
				text_result = await self._client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': """
							function() {
								return this.innerText || this.textContent || '';
							}
						""",
						'objectId': object_id,
						'returnByValue': True,
					},
					session_id=self._session_id,
				)
				
				if 'result' in text_result and 'value' in text_result['result']:
					return text_result['result']['value']
		except Exception as e:
			logger.error(f"Error getting text: {e}")
		
		return None

	async def get_attribute(self, name: str) -> str | None:
		"""Get attribute value of the element."""
		try:
			node_id = await self._get_node_id()
			params: 'GetAttributesParameters' = {'nodeId': node_id}
			result = await self._client.send.DOM.getAttributes(params, session_id=self._session_id)
			
			attributes = result.get('attributes', [])
			for i in range(0, len(attributes), 2):
				if attributes[i] == name:
					return attributes[i + 1]
		except Exception as e:
			logger.error(f"Error getting attribute: {e}")
		
		return None

	async def is_visible(self) -> bool:
		"""Check if element is visible using visual AI and DOM checks."""
		try:
			# DOM-based visibility check
			result = await self._client.send.DOM.resolveNode(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				
				visibility_result = await self._client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': """
							function() {
								const style = window.getComputedStyle(this);
								if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
									return false;
								}
								const rect = this.getBoundingClientRect();
								return rect.width > 0 && rect.height > 0;
							}
						""",
						'objectId': object_id,
						'returnByValue': True,
					},
					session_id=self._session_id,
				)
				
				if 'result' in visibility_result and 'value' in visibility_result['result']:
					return visibility_result['result']['value']
			
			# Visual AI check as fallback
			visual_info = await self.get_visual_info()
			if visual_info:
				return visual_info.get('confidence', 0) > 0.7
			
		except Exception as e:
			logger.error(f"Error checking visibility: {e}")
		
		return False

	async def get_element_type(self) -> str | None:
		"""Get element type using visual AI analysis."""
		visual_info = await self.get_visual_info()
		if visual_info:
			return visual_info.get('element_type')
		
		# Fallback to DOM-based inference
		try:
			result = await self._client.send.DOM.resolveNode(
				params={'backendNodeId': self._backend_node_id}, session_id=self._session_id
			)
			if 'object' in result and 'objectId' in result['object']:
				object_id = result['object']['objectId']
				
				type_result = await self._client.send.Runtime.callFunctionOn(
					params={
						'functionDeclaration': """
							function() {
								const tag = this.tagName.toLowerCase();
								const role = this.getAttribute('role');
								const type = this.getAttribute('type');
								
								if (role) return role;
								if (tag === 'a') return 'link';
								if (tag === 'button') return 'button';
								if (tag === 'input') return type || 'input';
								if (tag === 'select') return 'select';
								if (tag === 'textarea') return 'textarea';
								return tag;
							}
						""",
						'objectId': object_id,
						'returnByValue': True,
					},
					session_id=self._session_id,
				)
				
				if 'result' in type_result and 'value' in type_result['result']:
					return type_result['result']['value']
		except Exception:
			pass
		
		return None

	async def highlight(self, duration: float = 2.0, color: str = 'red') -> None:
		"""Highlight element using visual AI bounding box."""
		try:
			visual_info = await self.get_visual_info()
			if not visual_info or not visual_info.get('boundingBox'):
				return
			
			bbox = visual_info['boundingBox']
			
			# Create highlight overlay
			highlight_script = """
				(function() {
					const overlay = document.createElement('div');
					overlay.style.position = 'fixed';
					overlay.style.left = '%spx';
					overlay.style.top = '%spx';
					overlay.style.width = '%spx';
					overlay.style.height = '%spx';
					overlay.style.border = '3px solid %s';
					overlay.style.backgroundColor = '%s33';
					overlay.style.zIndex = '999999';
					overlay.style.pointerEvents = 'none';
					document.body.appendChild(overlay);
					
					setTimeout(() => {
						document.body.removeChild(overlay);
					}, %s);
				})();
			""" % (
				bbox['x'], bbox['y'], bbox['width'], bbox['height'],
				color, color, duration * 1000
			)
			
			await self._client.send.Runtime.evaluate(
				params={'expression': highlight_script},
				session_id=self._session_id
			)
			
		except Exception as e:
			logger.error(f"Error highlighting element: {e}")