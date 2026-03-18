from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from veil.agent.views import AgentHistoryList
from veil.browser.views import PLACEHOLDER_4PX_SCREENSHOT
from veil.config import CONFIG

if TYPE_CHECKING:
	from PIL import Image, ImageFont

logger = logging.getLogger(__name__)


def decode_unicode_escapes_to_utf8(text: str) -> str:
	"""Handle decoding any unicode escape sequences embedded in a string (needed to render non-ASCII languages like chinese or arabic in the GIF overlay text)"""

	if r'\u' not in text:
		# doesn't have any escape sequences that need to be decoded
		return text

	try:
		# Try to decode Unicode escape sequences
		return text.encode('latin1').decode('unicode_escape')
	except (UnicodeEncodeError, UnicodeDecodeError):
		# logger.debug(f"Failed to decode unicode escape sequences while generating gif text: {text}")
		return text


def create_history_gif(
	task: str,
	history: AgentHistoryList,
	#
	output_path: str = 'agent_history.gif',
	duration: int = 3000,
	show_goals: bool = True,
	show_task: bool = True,
	show_logo: bool = False,
	font_size: int = 40,
	title_font_size: int = 56,
	goal_font_size: int = 44,
	margin: int = 40,
	line_spacing: float = 1.5,
) -> None:
	"""Create a GIF from the agent's history with overlaid task and goal text."""
	if not history.history:
		logger.warning('No history to create GIF from')
		return

	from PIL import Image, ImageFont

	images = []

	# if history is empty, we can't create a gif
	if not history.history:
		logger.warning('No history to create GIF from')
		return

	# Get all screenshots from history (including None placeholders)
	screenshots = history.screenshots(return_none_if_not_screenshot=True)

	if not screenshots:
		logger.warning('No screenshots found in history')
		return

	# Find the first non-placeholder screenshot
	# A screenshot is considered a placeholder if:
	# 1. It's the exact 4px placeholder for about:blank pages, OR
	# 2. It comes from a new tab page (chrome://newtab/, about:blank, etc.)
	first_real_screenshot = None
	for screenshot in screenshots:
		if screenshot and screenshot != PLACEHOLDER_4PX_SCREENSHOT:
			first_real_screenshot = screenshot
			break

	if not first_real_screenshot:
		logger.warning('No valid screenshots found (all are placeholders or from new tab pages)')
		return

	# Try to load nicer fonts
	try:
		# Try different font options in order of preference
		# ArialUni is a font that comes with Office and can render most non-alphabet characters
		font_options = [
			'PingFang',
			'STHeiti Medium',
			'Microsoft YaHei',  # 微软雅黑
			'SimHei',  # 黑体
			'SimSun',  # 宋体
			'Noto Sans CJK SC',  # 思源黑体
			'WenQuanYi Micro Hei',  # 文泉驿微米黑
			'Helvetica',
			'Arial',
			'DejaVuSans',
			'Verdana',
		]
		font_loaded = False

		for font_name in font_options:
			try:
				if platform.system() == 'Windows':
					# Need to specify the abs font path on Windows
					font_name = os.path.join(CONFIG.WIN_FONT_DIR, font_name + '.ttf')
				regular_font = ImageFont.truetype(font_name, font_size)
				title_font = ImageFont.truetype(font_name, title_font_size)
				font_loaded = True
				break
			except OSError:
				continue

		if not font_loaded:
			raise OSError('No preferred fonts found')

	except OSError:
		regular_font = ImageFont.load_default()
		title_font = ImageFont.load_default()

	# Load logo if requested
	logo = None
	if show_logo:
		try:
			logo = Image.open('./static/veil.png')
			# Resize logo to be small (e.g., 40px height)
			logo_height = 150
			aspect_ratio = logo.width / logo.height
			logo_width = int(logo_height * aspect_ratio)
			logo = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)
		except Exception as e:
			logger.warning(f'Could not load logo: {e}')

	# Create task frame if requested
	if show_task and task:
		# Find the first non-placeholder screenshot for the task frame
		first_real_screenshot = None
		for item in history.history:
			screenshot_b64 = item.state.get_screenshot()
			if screenshot_b64 and screenshot_b64 != PLACEHOLDER_4PX_SCREENSHOT:
				first_real_screenshot = screenshot_b64
				break

		if first_real_screenshot:
			task_frame = _create_task_frame(
				task,
				first_real_screenshot,
				title_font,  # type: ignore
				regular_font,  # type: ignore
				logo,
				line_spacing,
			)
			images.append(task_frame)
		else:
			logger.warning('No real screenshots found for task frame, skipping task frame')

	# Process each history item with its corresponding screenshot
	for i, (item, screenshot) in enumerate(zip(history.history, screenshots), 1):
		if not screenshot:
			continue

		# Skip placeholder screenshots from about:blank pages
		# These are 4x4 white PNGs encoded as a specific base64 string
		if screenshot == PLACEHOLDER_4PX_SCREENSHOT:
			logger.debug(f'Skipping placeholder screenshot from about:blank page at step {i}')
			continue

		# Skip screenshots from new tab pages
		from veil.utils import is_new_tab_page

		if is_new_tab_page(item.state.url):
			logger.debug(f'Skipping screenshot from new tab page ({item.state.url}) at step {i}')
			continue

		# Convert base64 screenshot to PIL Image
		img_data = base64.b64decode(screenshot)
		image = Image.open(io.BytesIO(img_data))

		if show_goals and item.model_output:
			image = _add_overlay_to_image(
				image=image,
				step_number=i,
				goal_text=item.model_output.current_state.next_goal,
				regular_font=regular_font,  # type: ignore
				title_font=title_font,  # type: ignore
				margin=margin,
				logo=logo,
			)

		images.append(image)

	if images:
		# Save the GIF
		images[0].save(
			output_path,
			save_all=True,
			append_images=images[1:],
			duration=duration,
			loop=0,
			optimize=False,
		)
		logger.info(f'Created GIF at {output_path}')
	else:
		logger.warning('No images found in history to create GIF')


def create_history_replay(
	task: str,
	history: AgentHistoryList,
	output_path: str = 'agent_history_replay.html',
	include_network: bool = True,
	include_dom_snapshots: bool = True,
) -> None:
	"""Create an interactive HTML replay system for debugging automation sessions."""
	if not history.history:
		logger.warning('No history to create replay from')
		return

	from veil.utils import is_new_tab_page

	# Prepare session data
	session_data = {
		'task': task,
		'timestamp': datetime.now().isoformat(),
		'total_steps': len(history.history),
		'steps': [],
		'network_requests': [],
		'dom_snapshots': []
	}

	# Process each history step
	for i, item in enumerate(history.history, 1):
		step_data = {
			'step_number': i,
			'timestamp': item.timestamp.isoformat() if hasattr(item, 'timestamp') else datetime.now().isoformat(),
			'url': item.state.url,
			'title': item.state.title if hasattr(item.state, 'title') else '',
			'screenshot': item.state.get_screenshot() if item.state.get_screenshot() != PLACEHOLDER_4PX_SCREENSHOT else None,
			'action': item.model_output.action if item.model_output else None,
			'goal': item.model_output.current_state.next_goal if item.model_output else None,
			'thought': item.model_output.current_state.thought if item.model_output else None,
			'evaluation': item.model_output.current_state.evaluation_previous_goal if item.model_output else None,
			'memory': item.model_output.current_state.memory if item.model_output else None,
			'error': item.error if hasattr(item, 'error') else None,
			'is_new_tab': is_new_tab_page(item.state.url),
			'page_metrics': {
				'load_time': item.state.page_load_time if hasattr(item.state, 'page_load_time') else None,
				'dom_content_loaded': item.state.dom_content_loaded if hasattr(item.state, 'dom_content_loaded') else None,
				'network_idle': item.state.network_idle if hasattr(item.state, 'network_idle') else None,
			}
		}
		session_data['steps'].append(step_data)

		# Add network requests if available
		if include_network and hasattr(item.state, 'network_requests'):
			for req in item.state.network_requests:
				network_entry = {
					'step': i,
					'url': req.get('url', ''),
					'method': req.get('method', ''),
					'status': req.get('status', ''),
					'type': req.get('type', ''),
					'timestamp': req.get('timestamp', ''),
					'duration': req.get('duration', ''),
					'size': req.get('size', ''),
				}
				session_data['network_requests'].append(network_entry)

		# Add DOM snapshot if available
		if include_dom_snapshots and hasattr(item.state, 'dom_snapshot'):
			dom_entry = {
				'step': i,
				'html': item.state.dom_snapshot,
				'url': item.state.url,
			}
			session_data['dom_snapshots'].append(dom_entry)

	# Generate HTML replay file
	html_content = _generate_replay_html(session_data, task)

	# Write to file
	with open(output_path, 'w', encoding='utf-8') as f:
		f.write(html_content)

	logger.info(f'Created interactive replay at {output_path}')


def _generate_replay_html(session_data: Dict[str, Any], task: str) -> str:
	"""Generate the HTML content for the interactive replay system."""

	# Convert session data to JSON for embedding
	session_json = json.dumps(session_data, ensure_ascii=False, indent=2)

	html_template = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Browser-Use Visual Debugger - {task[:50]}...</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #0f0f10;
            color: #e0e0e0;
            height: 100vh;
            overflow: hidden;
        }}

        .container {{
            display: flex;
            flex-direction: column;
            height: 100vh;
        }}

        .header {{
            background: #1a1a1b;
            padding: 16px 24px;
            border-bottom: 1px solid #333;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .header h1 {{
            font-size: 20px;
            font-weight: 600;
            color: #fff;
        }}

        .header .task {{
            font-size: 14px;
            color: #888;
            max-width: 60%;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .controls {{
            display: flex;
            gap: 12px;
            align-items: center;
        }}

        .control-btn {{
            background: #2a2a2b;
            border: 1px solid #444;
            color: #fff;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s;
        }}

        .control-btn:hover {{
            background: #3a3a3b;
            border-color: #555;
        }}

        .control-btn.active {{
            background: #0066ff;
            border-color: #0066ff;
        }}

        .speed-control {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .speed-slider {{
            width: 100px;
            height: 4px;
            background: #333;
            border-radius: 2px;
            -webkit-appearance: none;
        }}

        .speed-slider::-webkit-slider-thumb {{
            -webkit-appearance: none;
            width: 16px;
            height: 16px;
            background: #0066ff;
            border-radius: 50%;
            cursor: pointer;
        }}

        .main-content {{
            display: flex;
            flex: 1;
            overflow: hidden;
        }}

        .timeline-panel {{
            width: 300px;
            background: #1a1a1b;
            border-right: 1px solid #333;
            display: flex;
            flex-direction: column;
        }}

        .timeline-header {{
            padding: 16px;
            border-bottom: 1px solid #333;
            font-weight: 600;
        }}

        .timeline-steps {{
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }}

        .step-item {{
            background: #252526;
            border: 1px solid #333;
            border-radius: 8px;
            margin-bottom: 8px;
            padding: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .step-item:hover {{
            background: #2a2a2b;
            border-color: #444;
        }}

        .step-item.active {{
            background: #0066ff22;
            border-color: #0066ff;
        }}

        .step-item.error {{
            background: #ff444422;
            border-color: #ff4444;
        }}

        .step-header {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
        }}

        .step-number {{
            font-weight: 600;
            color: #fff;
        }}

        .step-time {{
            font-size: 12px;
            color: #888;
        }}

        .step-goal {{
            font-size: 13px;
            color: #ccc;
            margin-bottom: 6px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .step-url {{
            font-size: 11px;
            color: #666;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .viewer-panel {{
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #0f0f10;
        }}

        .viewer-tabs {{
            display: flex;
            background: #1a1a1b;
            border-bottom: 1px solid #333;
        }}

        .viewer-tab {{
            padding: 12px 24px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            font-size: 14px;
            color: #888;
            transition: all 0.2s;
        }}

        .viewer-tab:hover {{
            color: #fff;
        }}

        .viewer-tab.active {{
            color: #fff;
            border-bottom-color: #0066ff;
        }}

        .viewer-content {{
            flex: 1;
            position: relative;
            overflow: hidden;
        }}

        .screenshot-viewer {{
            position: absolute;
            inset: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
        }}

        .screenshot-image {{
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }}

        .details-panel {{
            position: absolute;
            right: 0;
            top: 0;
            bottom: 0;
            width: 400px;
            background: #1a1a1b;
            border-left: 1px solid #333;
            overflow-y: auto;
            padding: 20px;
            transform: translateX(100%);
            transition: transform 0.3s;
        }}

        .details-panel.visible {{
            transform: translateX(0);
        }}

        .detail-section {{
            margin-bottom: 24px;
        }}

        .detail-section h3 {{
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid #333;
        }}

        .detail-content {{
            font-size: 13px;
            color: #ccc;
            line-height: 1.5;
        }}

        .detail-content pre {{
            background: #252526;
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            font-size: 12px;
            margin-top: 8px;
        }}

        .network-viewer, .dom-viewer {{
            position: absolute;
            inset: 0;
            background: #1a1a1b;
            padding: 20px;
            overflow-y: auto;
            display: none;
        }}

        .network-viewer.visible, .dom-viewer.visible {{
            display: block;
        }}

        .network-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        .network-table th {{
            text-align: left;
            padding: 12px;
            background: #252526;
            border-bottom: 1px solid #333;
            font-weight: 600;
        }}

        .network-table td {{
            padding: 12px;
            border-bottom: 1px solid #252526;
        }}

        .network-table tr:hover {{
            background: #252526;
        }}

        .method-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }}

        .method-get {{ background: #0066ff22; color: #0066ff; }}
        .method-post {{ background: #00ff6622; color: #00ff66; }}
        .method-put {{ background: #ffaa0022; color: #ffaa00; }}
        .method-delete {{ background: #ff444422; color: #ff4444; }}

        .status-success {{ color: #00ff66; }}
        .status-error {{ color: #ff4444; }}

        .timeline-controls {{
            display: flex;
            gap: 8px;
            padding: 12px;
            background: #252526;
            border-top: 1px solid #333;
        }}

        .timeline-progress {{
            flex: 1;
            height: 4px;
            background: #333;
            border-radius: 2px;
            position: relative;
            cursor: pointer;
        }}

        .timeline-progress-bar {{
            height: 100%;
            background: #0066ff;
            border-radius: 2px;
            width: 0%;
            transition: width 0.1s;
        }}

        .timeline-markers {{
            position: absolute;
            top: -8px;
            left: 0;
            right: 0;
            height: 20px;
        }}

        .timeline-marker {{
            position: absolute;
            width: 8px;
            height: 8px;
            background: #666;
            border-radius: 50%;
            transform: translateX(-50%);
            cursor: pointer;
        }}

        .timeline-marker.active {{
            background: #0066ff;
            transform: translateX(-50%) scale(1.2);
        }}

        .timeline-marker.error {{
            background: #ff4444;
        }}

        .stats-bar {{
            display: flex;
            gap: 24px;
            padding: 12px 24px;
            background: #252526;
            border-top: 1px solid #333;
            font-size: 13px;
        }}

        .stat-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .stat-label {{
            color: #888;
        }}

        .stat-value {{
            color: #fff;
            font-weight: 600;
        }}

        .loading-overlay {{
            position: absolute;
            inset: 0;
            background: rgba(0, 0, 0, 0.8);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }}

        .loading-spinner {{
            width: 40px;
            height: 40px;
            border: 3px solid #333;
            border-top-color: #0066ff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }}

        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}

        .keyboard-shortcuts {{
            position: fixed;
            bottom: 20px;
            left: 20px;
            background: #252526;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid #333;
            font-size: 12px;
            color: #888;
        }}

        .keyboard-shortcuts kbd {{
            background: #333;
            padding: 2px 6px;
            border-radius: 4px;
            margin: 0 2px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🔍 Visual Debugging & Replay</h1>
                <div class="task" id="task-display"></div>
            </div>
            <div class="controls">
                <div class="speed-control">
                    <span>Speed:</span>
                    <input type="range" class="speed-slider" id="speed-slider" min="0.1" max="5" step="0.1" value="1">
                    <span id="speed-value">1x</span>
                </div>
                <button class="control-btn" id="play-btn">▶ Play</button>
                <button class="control-btn" id="pause-btn">⏸ Pause</button>
                <button class="control-btn" id="step-back-btn">⏮ Step Back</button>
                <button class="control-btn" id="step-forward-btn">⏭ Step Forward</button>
                <button class="control-btn" id="details-toggle">📊 Details</button>
                <button class="control-btn" id="export-btn">💾 Export</button>
            </div>
        </div>

        <div class="main-content">
            <div class="timeline-panel">
                <div class="timeline-header">
                    <span>Timeline</span>
                    <span id="step-counter">0 / 0</span>
                </div>
                <div class="timeline-steps" id="timeline-steps"></div>
                <div class="timeline-controls">
                    <div class="timeline-progress" id="timeline-progress">
                        <div class="timeline-progress-bar" id="timeline-progress-bar"></div>
                        <div class="timeline-markers" id="timeline-markers"></div>
                    </div>
                </div>
            </div>

            <div class="viewer-panel">
                <div class="viewer-tabs">
                    <div class="viewer-tab active" data-tab="screenshot">Screenshot</div>
                    <div class="viewer-tab" data-tab="network">Network</div>
                    <div class="viewer-tab" data-tab="dom">DOM</div>
                </div>
                <div class="viewer-content">
                    <div class="screenshot-viewer" id="screenshot-viewer">
                        <img class="screenshot-image" id="screenshot-image" src="" alt="Screenshot">
                        <div class="loading-overlay" id="loading-overlay">
                            <div class="loading-spinner"></div>
                        </div>
                    </div>
                    <div class="network-viewer" id="network-viewer">
                        <table class="network-table">
                            <thead>
                                <tr>
                                    <th>Method</th>
                                    <th>URL</th>
                                    <th>Status</th>
                                    <th>Type</th>
                                    <th>Time</th>
                                    <th>Size</th>
                                </tr>
                            </thead>
                            <tbody id="network-table-body"></tbody>
                        </table>
                    </div>
                    <div class="dom-viewer" id="dom-viewer">
                        <pre id="dom-content"></pre>
                    </div>
                    <div class="details-panel" id="details-panel">
                        <div class="detail-section">
                            <h3>Current Step</h3>
                            <div class="detail-content" id="step-details"></div>
                        </div>
                        <div class="detail-section">
                            <h3>AI Thought Process</h3>
                            <div class="detail-content" id="thought-details"></div>
                        </div>
                        <div class="detail-section">
                            <h3>Action Taken</h3>
                            <div class="detail-content" id="action-details"></div>
                        </div>
                        <div class="detail-section">
                            <h3>Evaluation</h3>
                            <div class="detail-content" id="evaluation-details"></div>
                        </div>
                        <div class="detail-section">
                            <h3>Memory</h3>
                            <div class="detail-content" id="memory-details"></div>
                        </div>
                        <div class="detail-section">
                            <h3>Page Metrics</h3>
                            <div class="detail-content" id="metrics-details"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="stats-bar">
            <div class="stat-item">
                <span class="stat-label">Total Steps:</span>
                <span class="stat-value" id="total-steps">0</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Duration:</span>
                <span class="stat-value" id="total-duration">0s</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Success Rate:</span>
                <span class="stat-value" id="success-rate">0%</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Current URL:</span>
                <span class="stat-value" id="current-url">-</span>
            </div>
        </div>
    </div>

    <div class="keyboard-shortcuts">
        <div><kbd>Space</kbd> Play/Pause</div>
        <div><kbd>←</kbd> <kbd>→</kbd> Step through</div>
        <div><kbd>D</kbd> Toggle details</div>
        <div><kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> Switch tabs</div>
    </div>

    <script>
        // Session data embedded from Python
        const sessionData = {session_json};

        // State management
        let currentStep = 0;
        let isPlaying = false;
        let playbackSpeed = 1;
        let playbackInterval = null;
        let detailsVisible = false;

        // DOM elements
        const taskDisplay = document.getElementById('task-display');
        const timelineSteps = document.getElementById('timeline-steps');
        const timelineProgressBar = document.getElementById('timeline-progress-bar');
        const timelineMarkers = document.getElementById('timeline-markers');
        const stepCounter = document.getElementById('step-counter');
        const screenshotImage = document.getElementById('screenshot-image');
        const loadingOverlay = document.getElementById('loading-overlay');
        const networkTableBody = document.getElementById('network-table-body');
        const domContent = document.getElementById('dom-content');
        const detailsPanel = document.getElementById('details-panel');
        const speedSlider = document.getElementById('speed-slider');
        const speedValue = document.getElementById('speed-value');
        const totalStepsEl = document.getElementById('total-steps');
        const totalDurationEl = document.getElementById('total-duration');
        const successRateEl = document.getElementById('success-rate');
        const currentUrlEl = document.getElementById('current-url');

        // Initialize the replay system
        function init() {{
            taskDisplay.textContent = sessionData.task;
            totalStepsEl.textContent = sessionData.total_steps;

            // Calculate success rate
            const successfulSteps = sessionData.steps.filter(step => !step.error).length;
            const successRate = Math.round((successfulSteps / sessionData.total_steps) * 100);
            successRateEl.textContent = successRate + '%';

            // Calculate total duration
            if (sessionData.steps.length > 1) {{
                const firstTime = new Date(sessionData.steps[0].timestamp);
                const lastTime = new Date(sessionData.steps[sessionData.steps.length - 1].timestamp);
                const duration = Math.round((lastTime - firstTime) / 1000);
                totalDurationEl.textContent = duration + 's';
            }}

            // Build timeline
            buildTimeline();

            // Set initial step
            goToStep(0);

            // Setup event listeners
            setupEventListeners();
        }}

        function buildTimeline() {{
            timelineSteps.innerHTML = '';
            timelineMarkers.innerHTML = '';

            sessionData.steps.forEach((step, index) => {{
                // Create timeline item
                const stepItem = document.createElement('div');
                stepItem.className = `step-item ${{step.error ? 'error' : ''}}`;
                stepItem.dataset.step = index;
                stepItem.innerHTML = `
                    <div class="step-header">
                        <span class="step-number">Step ${{step.step_number}}</span>
                        <span class="step-time">${{formatTime(step.timestamp)}}</span>
                    </div>
                    <div class="step-goal">${{step.goal || 'No goal specified'}}</div>
                    <div class="step-url">${{step.url}}</div>
                `;
                stepItem.addEventListener('click', () => goToStep(index));
                timelineSteps.appendChild(stepItem);

                // Create timeline marker
                const marker = document.createElement('div');
                marker.className = `timeline-marker ${{step.error ? 'error' : ''}}`;
                marker.style.left = `${{(index / (sessionData.steps.length - 1)) * 100}}%`;
                marker.dataset.step = index;
                marker.addEventListener('click', (e) => {{
                    e.stopPropagation();
                    goToStep(index);
                }});
                timelineMarkers.appendChild(marker);
            }});
        }}

        function goToStep(stepIndex) {{
            if (stepIndex < 0 || stepIndex >= sessionData.steps.length) return;

            currentStep = stepIndex;
            const step = sessionData.steps[currentStep];

            // Update UI
            updateStepDisplay(step);
            updateTimeline();
            updateDetails(step);
            updateNetwork(step);
            updateDOM(step);

            // Update stats
            currentUrlEl.textContent = step.url.length > 50 ? step.url.substring(0, 50) + '...' : step.url;
        }}

        function updateStepDisplay(step) {{
            // Show/hide loading overlay
            loadingOverlay.style.display = step.screenshot ? 'none' : 'flex';

            // Update screenshot
            if (step.screenshot) {{
                screenshotImage.src = `data:image/png;base64,${{step.screenshot}}`;
                screenshotImage.style.display = 'block';
            }} else {{
                screenshotImage.style.display = 'none';
            }}

            // Update step counter
            stepCounter.textContent = `${{currentStep + 1}} / ${{sessionData.steps.length}}`;
        }}

        function updateTimeline() {{
            // Update progress bar
            const progress = (currentStep / (sessionData.steps.length - 1)) * 100;
            timelineProgressBar.style.width = `${{progress}}%`;

            // Update active states
            document.querySelectorAll('.step-item').forEach((item, index) => {{
                item.classList.toggle('active', index === currentStep);
            }});

            document.querySelectorAll('.timeline-marker').forEach((marker, index) => {{
                marker.classList.toggle('active', index === currentStep);
            }});

            // Scroll to active step
            const activeStep = document.querySelector(`.step-item[data-step="${{currentStep}}"]`);
            if (activeStep) {{
                activeStep.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
        }}

        function updateDetails(step) {{
            document.getElementById('step-details').innerHTML = `
                <p><strong>Step:</strong> ${{step.step_number}}</p>
                <p><strong>URL:</strong> ${{step.url}}</p>
                <p><strong>Title:</strong> ${{step.title || 'N/A'}}</p>
                <p><strong>Timestamp:</strong> ${{new Date(step.timestamp).toLocaleString()}}</p>
                ${{step.is_new_tab ? '<p><strong>Note:</strong> New tab page</p>' : ''}}
                ${{step.error ? `<p><strong style="color: #ff4444;">Error:</strong> ${{step.error}}</p>` : ''}}
            `;

            document.getElementById('thought-details').innerHTML = `
                <p>${{step.thought || 'No thought recorded'}}</p>
            `;

            document.getElementById('action-details').innerHTML = `
                <pre>${{JSON.stringify(step.action, null, 2) || 'No action recorded'}}</pre>
            `;

            document.getElementById('evaluation-details').innerHTML = `
                <p>${{step.evaluation || 'No evaluation recorded'}}</p>
            `;

            document.getElementById('memory-details').innerHTML = `
                <p>${{step.memory || 'No memory recorded'}}</p>
            `;

            document.getElementById('metrics-details').innerHTML = `
                <p><strong>Load Time:</strong> ${{step.page_metrics.load_time || 'N/A'}}ms</p>
                <p><strong>DOM Content Loaded:</strong> ${{step.page_metrics.dom_content_loaded || 'N/A'}}ms</p>
                <p><strong>Network Idle:</strong> ${{step.page_metrics.network_idle ? 'Yes' : 'No'}}</p>
            `;
        }}

        function updateNetwork(step) {{
            const stepRequests = sessionData.network_requests.filter(req => req.step === step.step_number);
            networkTableBody.innerHTML = '';

            stepRequests.forEach(req => {{
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td><span class="method-badge method-${{req.method.toLowerCase()}}">${{req.method}}</span></td>
                    <td title="${{req.url}}">${{truncate(req.url, 50)}}</td>
                    <td class="${{req.status >= 400 ? 'status-error' : 'status-success'}}">${{req.status}}</td>
                    <td>${{req.type}}</td>
                    <td>${{req.duration}}ms</td>
                    <td>${{formatBytes(req.size)}}</td>
                `;
                networkTableBody.appendChild(row);
            }});

            if (stepRequests.length === 0) {{
                networkTableBody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #666;">No network requests for this step</td></tr>';
            }}
        }}

        function updateDOM(step) {{
            const domSnapshot = sessionData.dom_snapshots.find(dom => dom.step === step.step_number);
            if (domSnapshot) {{
                domContent.textContent = domSnapshot.html;
            }} else {{
                domContent.textContent = 'No DOM snapshot available for this step';
            }}
        }}

        function play() {{
            if (isPlaying) return;
            isPlaying = true;
            document.getElementById('play-btn').classList.add('active');
            document.getElementById('pause-btn').classList.remove('active');

            playbackInterval = setInterval(() => {{
                if (currentStep < sessionData.steps.length - 1) {{
                    goToStep(currentStep + 1);
                }} else {{
                    pause();
                }}
            }}, 2000 / playbackSpeed);
        }}

        function pause() {{
            isPlaying = false;
            document.getElementById('play-btn').classList.remove('active');
            document.getElementById('pause-btn').classList.add('active');

            if (playbackInterval) {{
                clearInterval(playbackInterval);
                playbackInterval = null;
            }}
        }}

        function stepForward() {{
            pause();
            if (currentStep < sessionData.steps.length - 1) {{
                goToStep(currentStep + 1);
            }}
        }}

        function stepBack() {{
            pause();
            if (currentStep > 0) {{
                goToStep(currentStep - 1);
            }}
        }}

        function toggleDetails() {{
            detailsVisible = !detailsVisible;
            detailsPanel.classList.toggle('visible', detailsVisible);
            document.getElementById('details-toggle').classList.toggle('active', detailsVisible);
        }}

        function switchTab(tabName) {{
            document.querySelectorAll('.viewer-tab').forEach(tab => {{
                tab.classList.toggle('active', tab.dataset.tab === tabName);
            }});

            document.getElementById('screenshot-viewer').style.display = tabName === 'screenshot' ? 'flex' : 'none';
            document.getElementById('network-viewer').classList.toggle('visible', tabName === 'network');
            document.getElementById('dom-viewer').classList.toggle('visible', tabName === 'dom');
        }}

        function exportSession() {{
            const dataStr = JSON.stringify(sessionData, null, 2);
            const dataBlob = new Blob([dataStr], {{ type: 'application/json' }});
            const url = URL.createObjectURL(dataBlob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `veil-session-${{new Date().toISOString().slice(0,19)}}.json`;
            link.click();
            URL.revokeObjectURL(url);
        }}

        function setupEventListeners() {{
            // Control buttons
            document.getElementById('play-btn').addEventListener('click', play);
            document.getElementById('pause-btn').addEventListener('click', pause);
            document.getElementById('step-forward-btn').addEventListener('click', stepForward);
            document.getElementById('step-back-btn').addEventListener('click', stepBack);
            document.getElementById('details-toggle').addEventListener('click', toggleDetails);
            document.getElementById('export-btn').addEventListener('click', exportSession);

            // Speed slider
            speedSlider.addEventListener('input', (e) => {{
                playbackSpeed = parseFloat(e.target.value);
                speedValue.textContent = playbackSpeed + 'x';
                if (isPlaying) {{
                    pause();
                    play();
                }}
            }});

            // Timeline progress click
            document.getElementById('timeline-progress').addEventListener('click', (e) => {{
                const rect = e.target.getBoundingClientRect();
                const clickX = e.clientX - rect.left;
                const progress = clickX / rect.width;
                const stepIndex = Math.round(progress * (sessionData.steps.length - 1));
                goToStep(stepIndex);
            }});

            // Tab switching
            document.querySelectorAll('.viewer-tab').forEach(tab => {{
                tab.addEventListener('click', () => switchTab(tab.dataset.tab));
            }});

            // Keyboard shortcuts
            document.addEventListener('keydown', (e) => {{
                if (e.target.tagName === 'INPUT') return;

                switch(e.key) {{
                    case ' ':
                        e.preventDefault();
                        isPlaying ? pause() : play();
                        break;
                    case 'ArrowRight':
                        e.preventDefault();
                        stepForward();
                        break;
                    case 'ArrowLeft':
                        e.preventDefault();
                        stepBack();
                        break;
                    case 'd':
                    case 'D':
                        e.preventDefault();
                        toggleDetails();
                        break;
                    case '1':
                        switchTab('screenshot');
                        break;
                    case '2':
                        switchTab('network');
                        break;
                    case '3':
                        switchTab('dom');
                        break;
                }}
            }});
        }}

        // Utility functions
        function formatTime(isoString) {{
            const date = new Date(isoString);
            return date.toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
        }}

        function truncate(str, length) {{
            return str.length > length ? str.substring(0, length) + '...' : str;
        }}

        function formatBytes(bytes) {{
            if (!bytes || bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }}

        // Initialize when DOM is loaded
        document.addEventListener('DOMContentLoaded', init);
    </script>
</body>
</html>'''

	return html_template


def _create_task_frame(
	task: str,
	first_screenshot: str,
	title_font: ImageFont.FreeTypeFont,
	regular_font: ImageFont.FreeTypeFont,
	logo: Image.Image | None = None,
	line_spacing: float = 1.5,
) -> Image.Image:
	"""Create initial frame showing the task."""
	from PIL import Image, ImageDraw, ImageFont

	img_data = base64.b64decode(first_screenshot)
	template = Image.open(io.BytesIO(img_data))
	image = Image.new('RGB', template.size, (0, 0, 0))
	draw = ImageDraw.Draw(image)

	# Calculate vertical center of image
	center_y = image.height // 2

	# Draw task text with dynamic font size based on task length
	margin = 140  # Increased margin
	max_width = image.width - (2 * margin)

	# Dynamic font size calculation based on task length
	# Start with base font size (regular + 16)
	base_font_size = regular_font.size + 16
	min_font_size = max(regular_font.size - 10, 16)  # Don't go below 16pt
	# Calculate dynamic font size based on text length and complexity
	# Longer texts get progressively smaller fonts
	text_length = len(task)
	if text_length > 200:
		# For very long text, reduce font size logarithmically
		font_size = max(base_font_size - int(10 * (text_length / 200)), min_font_size)
	else:
		font_size = base_font_size

	# Try to create a larger font, but fall back to regular font if it fails
	try:
		larger_font = ImageFont.truetype(regular_font.path, font_size)  # type: ignore
	except (OSError, AttributeError):
		# Fall back to regular font if .path is not available or font loading fails
		larger_font = regular_font

	# Generate wrapped text with the calculated font size
	wrapped_text = _wrap_text(task, larger_font, max_width)

	# Calculate line height with spacing
	line_height = larger_font.size * line_spacing

	# Split text into lines and draw with custom spacing
	lines = wrapped_text.split('\n')
	total_height = line_height * len(lines)

	# Start position for first line
	text_y = center_y - (total_height / 2) + 50  # Shifted down slightly

	for line in lines:
		# Get line width for centering
		line_bbox = draw.textbbox((0, 0), line, font=larger_font)
		line_width = line_bbox[2] - line_bbox[0]
		text_x = (image.width - line_width) // 2

		# Draw text with shadow for better visibility
		draw.text((text_x + 2, text_y + 2), line, font=larger_font, fill=(0, 0, 0, 128))
		draw.text((text_x, text_y), line, font=larger_font, fill=(255, 255, 255, 255))

		text_y += line_height

	# Add logo if provided
	if logo:
		logo_x = image.width - logo.width - 20
		logo_y = 20
		image.paste(logo, (logo_x, logo_y), logo if logo.mode == 'RGBA' else None)

	return image


def _add_overlay_to_image(
	image: Image.Image,
	step_number: int,
	goal_text: str,
	regular_font: ImageFont.FreeTypeFont,
	title_font: ImageFont.FreeTypeFont,
	margin: int = 40,
	logo: Image.Image | None = None,
) -> Image.Image:
	"""Add overlay with step number and goal text to an image."""
	from PIL import ImageDraw

	draw = ImageDraw.Draw(image)

	# Add semi-transparent overlay at the bottom
	overlay_height = 200
	overlay = Image.new('RGBA', (image.width, overlay_height), (0, 0, 0, 180))
	image.paste(overlay, (0, image.height - overlay_height), overlay)

	# Add step number
	step_text = f"Step {step_number}"
	draw.text((margin, image.height - overlay_height + 20), step_text, font=title_font, fill=(255, 255, 255, 255))

	# Add goal text
	if goal_text:
		# Decode unicode escapes if needed
		goal_text = decode_unicode_escapes_to_utf8(goal_text)

		# Wrap text to fit within image width
		max_width = image.width - (2 * margin)
		wrapped_goal = _wrap_text(goal_text, regular_font, max_width)

		# Draw goal text
		goal_y = image.height - overlay_height + 80
		for line in wrapped_goal.split('\n'):
			draw.text((margin, goal_y), line, font=regular_font, fill=(200, 200, 200, 255))
			goal_y += regular_font.size * 1.2

	# Add logo if provided
	if logo:
		logo_x = image.width - logo.width - 20
		logo_y = 20
		image.paste(logo, (logo_x, logo_y), logo if logo.mode == 'RGBA' else None)

	return image


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
	"""Wrap text to fit within a given width."""
	words = text.split()
	lines = []
	current_line = []

	for word in words:
		# Test if adding this word exceeds the width
		test_line = ' '.join(current_line + [word])
		bbox = font.getbbox(test_line)
		line_width = bbox[2] - bbox[0]

		if line_width <= max_width:
			current_line.append(word)
		else:
			# If the current line is not empty, add it to lines
			if current_line:
				lines.append(' '.join(current_line))
				current_line = [word]
			else:
				# If the word itself is too long, force it on its own line
				lines.append(word)
				current_line = []

	# Add the last line
	if current_line:
		lines.append(' '.join(current_line))

	return '\n'.join(lines)