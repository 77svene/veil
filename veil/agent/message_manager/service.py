from __future__ import annotations

import logging
import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Any, Dict, List, Optional
from enum import Enum

from veil.agent.message_manager.views import (
	HistoryItem,
)
from veil.agent.prompts import AgentMessagePrompt
from veil.agent.views import (
	ActionResult,
	AgentOutput,
	AgentStepInfo,
	MessageCompactionSettings,
	MessageManagerState,
)
from veil.browser.views import BrowserStateSummary
from veil.filesystem.file_system import FileSystem
from veil.llm.base import BaseChatModel
from veil.llm.messages import (
	BaseMessage,
	ContentPartImageParam,
	ContentPartTextParam,
	SystemMessage,
	UserMessage,
)
from veil.observability import observe_debug
from veil.utils import match_url_with_domain_pattern, time_execution_sync

logger = logging.getLogger(__name__)

# Security and Compliance Constants
SENSITIVE_PATTERNS = [
	r'password\s*[:=]\s*["\']?([^"\'\s]+)',
	r'token\s*[:=]\s*["\']?([^"\'\s]+)',
	r'api[_-]?key\s*[:=]\s*["\']?([^"\'\s]+)',
	r'secret\s*[:=]\s*["\']?([^"\'\s]+)',
	r'authorization\s*[:=]\s*["\']?([^"\'\s]+)',
	r'bearer\s+([a-zA-Z0-9\-._~+/]+=*)',
	r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b',  # SSN pattern
	r'\b\d{16}\b',  # Credit card pattern
	r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email pattern
]

class ComplianceFramework(Enum):
	GDPR = "gdpr"
	SOC2 = "soc2"
	HIPAA = "hipaa"
	PCI_DSS = "pci_dss"

class AuditEventType(Enum):
	AGENT_START = "agent_start"
	AGENT_STOP = "agent_stop"
	STEP_START = "step_start"
	STEP_COMPLETE = "step_complete"
	SENSITIVE_DATA_ACCESS = "sensitive_data_access"
	SECRET_MANAGER_ACCESS = "secret_manager_access"
	COMPLIANCE_CHECK = "compliance_check"
	ERROR = "error"
	USER_ACTION = "user_action"

class AuditLogger:
	"""Tamper-proof audit logging for compliance requirements"""
	
	def __init__(self, compliance_frameworks: List[ComplianceFramework] = None):
		self.compliance_frameworks = compliance_frameworks or [ComplianceFramework.GDPR, ComplianceFramework.SOC2]
		self.audit_trail: List[Dict[str, Any]] = []
		self.session_id = self._generate_session_id()
		self._initialize_audit_log()
	
	def _generate_session_id(self) -> str:
		"""Generate unique session ID for audit trail"""
		timestamp = datetime.utcnow().isoformat()
		return hashlib.sha256(f"{timestamp}:{id(self)}".encode()).hexdigest()[:16]
	
	def _initialize_audit_log(self):
		"""Initialize audit log with session metadata"""
		self.log_event(
			event_type=AuditEventType.AGENT_START,
			details={
				"session_id": self.session_id,
				"compliance_frameworks": [f.value for f in self.compliance_frameworks],
				"timestamp": datetime.utcnow().isoformat()
			}
		)
	
	def log_event(self, event_type: AuditEventType, details: Dict[str, Any], 
				  sensitive_data_redacted: bool = True):
		"""Log an audit event with tamper-proof hashing"""
		event = {
			"event_id": self._generate_event_id(),
			"timestamp": datetime.utcnow().isoformat(),
			"session_id": self.session_id,
			"event_type": event_type.value,
			"details": details,
			"compliance_frameworks": [f.value for f in self.compliance_frameworks],
			"previous_hash": self._get_previous_hash()
		}
		
		# Generate hash for tamper detection
		event["event_hash"] = self._generate_event_hash(event)
		
		# Redact sensitive data if requested
		if sensitive_data_redacted:
			event["details"] = self._redact_sensitive_data(event["details"])
		
		self.audit_trail.append(event)
		
		# Also log to standard logger for immediate visibility
		logger.info(f"AUDIT: {event_type.value} - {json.dumps(event['details'], default=str)}")
		
		return event["event_id"]
	
	def _generate_event_id(self) -> str:
		"""Generate unique event ID"""
		timestamp = datetime.utcnow().timestamp()
		return hashlib.sha256(f"{timestamp}:{len(self.audit_trail)}".encode()).hexdigest()[:12]
	
	def _get_previous_hash(self) -> Optional[str]:
		"""Get hash of previous event for chain verification"""
		if not self.audit_trail:
			return None
		return self.audit_trail[-1].get("event_hash")
	
	def _generate_event_hash(self, event: Dict[str, Any]) -> str:
		"""Generate hash for event tamper detection"""
		# Create deterministic string representation
		event_copy = event.copy()
		event_copy.pop("event_hash", None)  # Remove hash field for calculation
		event_str = json.dumps(event_copy, sort_keys=True, default=str)
		return hashlib.sha256(event_str.encode()).hexdigest()
	
	def _redact_sensitive_data(self, data: Any) -> Any:
		"""Recursively redact sensitive data from audit logs"""
		if isinstance(data, dict):
			return {k: self._redact_sensitive_data(v) for k, v in data.items()}
		elif isinstance(data, list):
			return [self._redact_sensitive_data(item) for item in data]
		elif isinstance(data, str):
			# Apply redaction patterns
			redacted = data
			for pattern in SENSITIVE_PATTERNS:
				redacted = re.sub(pattern, '[REDACTED]', redacted, flags=re.IGNORECASE)
			return redacted
		else:
			return data
	
	def verify_integrity(self) -> bool:
		"""Verify audit trail integrity by checking hash chain"""
		for i in range(1, len(self.audit_trail)):
			current_event = self.audit_trail[i]
			previous_event = self.audit_trail[i-1]
			
			# Verify previous hash matches
			if current_event.get("previous_hash") != previous_event.get("event_hash"):
				return False
			
			# Verify current event hash
			expected_hash = self._generate_event_hash({
				k: v for k, v in current_event.items() if k != "event_hash"
			})
			if current_event.get("event_hash") != expected_hash:
				return False
		
		return True
	
	def export_audit_trail(self, filepath: Optional[str] = None) -> str:
		"""Export audit trail to file or return as JSON string"""
		export_data = {
			"metadata": {
				"session_id": self.session_id,
				"export_timestamp": datetime.utcnow().isoformat(),
				"total_events": len(self.audit_trail),
				"integrity_verified": self.verify_integrity()
			},
			"audit_trail": self.audit_trail
		}
		
		export_json = json.dumps(export_data, indent=2, default=str)
		
		if filepath:
			with open(filepath, 'w') as f:
				f.write(export_json)
		
		return export_json

class SecretManagerIntegration:
	"""Integration with external secret managers"""
	
	def __init__(self, secret_manager_type: str = "local", **kwargs):
		self.secret_manager_type = secret_manager_type
		self.config = kwargs
		self._secret_cache = {}
	
	def get_secret(self, secret_name: str, context: Optional[Dict] = None) -> Optional[str]:
		"""Retrieve secret from configured secret manager"""
		cache_key = f"{secret_name}:{json.dumps(context or {}, sort_keys=True)}"
		
		if cache_key in self._secret_cache:
			return self._secret_cache[cache_key]
		
		secret_value = None
		
		try:
			if self.secret_manager_type == "hashicorp_vault":
				secret_value = self._get_from_vault(secret_name, context)
			elif self.secret_manager_type == "aws_secrets_manager":
				secret_value = self._get_from_aws(secret_name, context)
			elif self.secret_manager_type == "local":
				secret_value = self._get_from_local(secret_name, context)
			else:
				logger.warning(f"Unsupported secret manager type: {self.secret_manager_type}")
		
		except Exception as e:
			logger.error(f"Failed to retrieve secret '{secret_name}': {e}")
		
		if secret_value:
			self._secret_cache[cache_key] = secret_value
		
		return secret_value
	
	def _get_from_vault(self, secret_name: str, context: Optional[Dict] = None) -> Optional[str]:
		"""Retrieve secret from HashiCorp Vault"""
		# Implementation would use hvac library
		# This is a placeholder for actual implementation
		logger.debug(f"Vault integration not implemented for secret: {secret_name}")
		return None
	
	def _get_from_aws(self, secret_name: str, context: Optional[Dict] = None) -> Optional[str]:
		"""Retrieve secret from AWS Secrets Manager"""
		# Implementation would use boto3
		# This is a placeholder for actual implementation
		logger.debug(f"AWS Secrets Manager integration not implemented for secret: {secret_name}")
		return None
	
	def _get_from_local(self, secret_name: str, context: Optional[Dict] = None) -> Optional[str]:
		"""Retrieve secret from local environment or configuration"""
		import os
		return os.getenv(secret_name)
	
	def clear_cache(self):
		"""Clear secret cache for security"""
		self._secret_cache.clear()

# ========== Logging Helper Functions ==========
# These functions are used ONLY for formatting debug log output.
# They do NOT affect the actual message content sent to the LLM.
# All logging functions start with _log_ for easy identification.

def _log_get_message_emoji(message: BaseMessage) -> str:
	"""Get emoji for a message type - used only for logging display"""
	emoji_map = {
		'UserMessage': '💬',
		'SystemMessage': '🧠',
		'AssistantMessage': '🔨',
	}
	return emoji_map.get(message.__class__.__name__, '🎮')

def _redact_for_logging(text: str) -> str:
	"""Redact sensitive data from text for logging purposes"""
	redacted = text
	for pattern in SENSITIVE_PATTERNS:
		redacted = re.sub(pattern, '[REDACTED]', redacted, flags=re.IGNORECASE)
	return redacted

def _log_format_message_line(message: BaseMessage, content: str, is_last_message: bool, terminal_width: int) -> list[str]:
	"""Format a single message for logging display with sensitive data redaction"""
	try:
		lines = []

		# Get emoji and token info
		emoji = _log_get_message_emoji(message)
		# token_str = str(message.metadata.tokens).rjust(4)
		# TODO: fix the token count
		token_str = '??? (TODO)'
		prefix = f'{emoji}[{token_str}]: '

		# Calculate available width (emoji=2 visual cols + [token]: =8 chars)
		content_width = terminal_width - 10

		# Redact sensitive data for logging
		redacted_content = _redact_for_logging(content)

		# Handle last message wrapping
		if is_last_message and len(redacted_content) > content_width:
			# Find a good break point
			break_point = redacted_content.rfind(' ', 0, content_width)
			if break_point > content_width * 0.7:  # Keep at least 70% of line
				first_line = redacted_content[:break_point]
				rest = redacted_content[break_point + 1 :]
			else:
				# No good break point, just truncate
				first_line = redacted_content[:content_width]
				rest = redacted_content[content_width:]

			lines.append(prefix + first_line)

			# Second line with 10-space indent
			if rest:
				if len(rest) > terminal_width - 10:
					rest = rest[: terminal_width - 10]
				lines.append(' ' * 10 + rest)
		else:
			# Single line - truncate if needed
			if len(redacted_content) > content_width:
				redacted_content = redacted_content[:content_width]
			lines.append(prefix + redacted_content)

		return lines
	except Exception as e:
		logger.warning(f'Failed to format message line for logging: {e}')
		# Return a simple fallback line
		return ['❓[   ?]: [Error formatting message]']

# ========== End of Logging Helper Functions ==========

class MessageManager:
	vision_detail_level: Literal['auto', 'low', 'high']

	def __init__(
		self,
		task: str,
		system_message: SystemMessage,
		file_system: FileSystem,
		state: MessageManagerState = MessageManagerState(),
		use_thinking: bool = True,
		include_attributes: list[str] | None = None,
		sensitive_data: dict[str, str | dict[str, str]] | None = None,
		max_history_items: int | None = None,
		vision_detail_level: Literal['auto', 'low', 'high'] = 'auto',
		include_tool_call_examples: bool = False,
		include_recent_events: bool = False,
		sample_images: list[ContentPartTextParam | ContentPartImageParam] | None = None,
		llm_screenshot_size: tuple[int, int] | None = None,
		max_clickable_elements_length: int = 40000,
		# Security and compliance parameters
		compliance_frameworks: List[ComplianceFramework] = None,
		secret_manager_config: Dict[str, Any] = None,
		enable_audit_logging: bool = True,
		redact_logs: bool = True,
	):
		self.task = task
		self.state = state
		self.system_prompt = system_message
		self.file_system = file_system
		self.sensitive_data_description = ''
		self.use_thinking = use_thinking
		self.max_history_items = max_history_items
		self.vision_detail_level = vision_detail_level
		self.include_tool_call_examples = include_tool_call_examples
		self.include_recent_events = include_recent_events
		self.sample_images = sample_images
		self.llm_screenshot_size = llm_screenshot_size
		self.max_clickable_elements_length = max_clickable_elements_length

		# Security and compliance initialization
		self.compliance_frameworks = compliance_frameworks or [ComplianceFramework.GDPR, ComplianceFramework.SOC2]
		self.enable_audit_logging = enable_audit_logging
		self.redact_logs = redact_logs
		
		# Initialize audit logger
		self.audit_logger = AuditLogger(self.compliance_frameworks) if enable_audit_logging else None
		
		# Initialize secret manager integration
		secret_config = secret_manager_config or {"type": "local"}
		self.secret_manager = SecretManagerIntegration(**secret_config)
		
		# Log initialization
		if self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.AGENT_START,
				{
					"task_length": len(task),
					"compliance_frameworks": [f.value for f in self.compliance_frameworks],
					"secret_manager_type": secret_config.get("type", "local")
				}
			)

		assert max_history_items is None or max_history_items > 5, 'max_history_items must be None or greater than 5'

		# Store settings as direct attributes instead of in a settings object
		self.include_attributes = include_attributes or []
		self.sensitive_data = sensitive_data
		self.last_input_messages = []
		self.last_state_message_text: str | None = None
		# Only initialize messages if state is empty
		if len(self.state.history.get_messages()) == 0:
			self._set_message_with_type(self.system_prompt, 'system')

	def _set_message_with_type(self, message: BaseMessage, message_type: str) -> None:
		"""Set message with audit logging"""
		self.state.history.add_message(message)
		
		if self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.USER_ACTION,
				{
					"action": "message_added",
					"message_type": message_type,
					"content_length": len(str(message.content)) if hasattr(message, 'content') else 0
				}
			)

	@property
	def agent_history_description(self) -> str:
		"""Build agent history description from list of items, respecting max_history_items limit"""
		compacted_prefix = ''
		if self.state.compacted_memory:
			compacted_prefix = f'<compacted_memory>\n{self.state.compacted_memory}\n</compacted_memory>\n'

		if self.max_history_items is None:
			# Include all items
			return compacted_prefix + '\n'.join(item.to_string() for item in self.state.agent_history_items)

		total_items = len(self.state.agent_history_items)

		# If we have fewer items than the limit, just return all items
		if total_items <= self.max_history_items:
			return compacted_prefix + '\n'.join(item.to_string() for item in self.state.agent_history_items)

		# We have more items than the limit, so we need to omit some
		omitted_count = total_items - self.max_history_items

		# Show first item + omitted message + most recent (max_history_items - 1) items
		# The omitted message doesn't count against the limit, only real history items do
		recent_items_count = self.max_history_items - 1  # -1 for first item

		items_to_include = [
			self.state.agent_history_items[0].to_string(),  # Keep first item (initialization)
			f'<sys>[... {omitted_count} previous steps omitted...]</sys>',
		]
		# Add most recent items
		items_to_include.extend([item.to_string() for item in self.state.agent_history_items[-recent_items_count:]])

		return compacted_prefix + '\n'.join(items_to_include)

	def add_new_task(self, new_task: str) -> None:
		"""Add new task with audit logging"""
		new_task = '<follow_up_user_request> ' + new_task.strip() + ' </follow_up_user_request>'
		if '<initial_user_request>' not in self.task:
			self.task = '<initial_user_request>' + self.task + '</initial_user_request>'
		self.task += '\n' + new_task
		task_update_item = HistoryItem(system_message=new_task)
		self.state.agent_history_items.append(task_update_item)
		
		# Audit log
		if self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.USER_ACTION,
				{
					"action": "task_added",
					"task_length": len(new_task),
					"total_tasks": len(self.state.agent_history_items)
				}
			)

	def _get_sensitive_data_description(self, url: str) -> str:
		"""Get description of sensitive data for current URL with compliance logging"""
		if not self.sensitive_data:
			return ""
		
		# Log sensitive data access for compliance
		if self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.SENSITIVE_DATA_ACCESS,
				{
					"url": url,
					"has_sensitive_data": bool(self.sensitive_data),
					"keys": list(self.sensitive_data.keys()) if isinstance(self.sensitive_data, dict) else []
				}
			)
		
		# Original implementation
		description_parts = []
		
		if isinstance(self.sensitive_data, dict):
			for key, value in self.sensitive_data.items():
				if isinstance(value, dict):
					# Check if this key matches the current domain
					if match_url_with_domain_pattern(url, key):
						for sub_key, sub_value in value.items():
							description_parts.append(f"- {sub_key}: {sub_value}")
				else:
					description_parts.append(f"- {key}: {value}")
		
		if description_parts:
			return "Sensitive data available:\n" + "\n".join(description_parts)
		return ""

	def prepare_step_state(
		self,
		browser_state_summary: BrowserStateSummary,
		model_output: AgentOutput | None = None,
		result: list[ActionResult] | None = None,
		step_info: AgentStepInfo | None = None,
		sensitive_data=None,
	) -> None:
		"""Prepare state for the next LLM call without building the final state message."""
		# Audit log step start
		if self.audit_logger and step_info:
			self.audit_logger.log_event(
				AuditEventType.STEP_START,
				{
					"step_number": step_info.step_number,
					"url": browser_state_summary.url,
					"has_model_output": model_output is not None,
					"has_result": result is not None
				}
			)
		
		self.state.history.context_messages.clear()
		self._update_agent_history_description(model_output, result, step_info)

		effective_sensitive_data = sensitive_data if sensitive_data is not None else self.sensitive_data
		if effective_sensitive_data is not None:
			self.sensitive_data = effective_sensitive_data
			self.sensitive_data_description = self._get_sensitive_data_description(browser_state_summary.url)
		
		# Log sensitive data access if any
		if self.audit_logger and effective_sensitive_data:
			self.audit_logger.log_event(
				AuditEventType.SENSITIVE_DATA_ACCESS,
				{
					"step": step_info.step_number if step_info else None,
					"url": browser_state_summary.url,
					"accessed_keys": list(effective_sensitive_data.keys()) if isinstance(effective_sensitive_data, dict) else []
				}
			)

	async def maybe_compact_messages(
		self,
		llm: BaseChatModel | None,
		settings: MessageCompactionSettings | None,
		step_info: AgentStepInfo | None = None,
	) -> bool:
		"""Summarize older history into a compact memory block.

		Step interval is the primary trigger; char count is a minimum floor.
		"""
		if not settings or not settings.enabled:
			return False
		if llm is None:
			return False
		if step_info is None:
			return False

		# Step cadence gate
		steps_since = step_info.step_number - (self.state.last_compaction_step or 0)
		if steps_since < settings.compact_every_n_steps:
			return False

		# Char floor gate
		history_items = self.state.agent_history_items
		total_chars = sum(len(item.to_string()) for item in history_items)
		if total_chars < settings.min_chars_before_compaction:
			return False

		# Perform compaction
		try:
			# Audit log compaction start
			if self.audit_logger:
				self.audit_logger.log_event(
					AuditEventType.STEP_START,
					{
						"action": "message_compaction_start",
						"step_number": step_info.step_number,
						"total_items": len(history_items),
						"total_chars": total_chars
					}
				)
			
			# Build compaction prompt
			compaction_prompt = self._build_compaction_prompt(history_items, step_info)
			
			# Call LLM for compaction
			messages = [UserMessage(content=compaction_prompt)]
			response = await llm.ainvoke(messages)
			
			# Update state
			self.state.compacted_memory = response.content
			self.state.last_compaction_step = step_info.step_number
			
			# Clear old history items (keep only recent ones)
			if len(history_items) > settings.keep_recent_n_items:
				self.state.agent_history_items = history_items[-settings.keep_recent_n_items:]
			
			# Audit log compaction complete
			if self.audit_logger:
				self.audit_logger.log_event(
					AuditEventType.STEP_COMPLETE,
					{
						"action": "message_compaction_complete",
						"step_number": step_info.step_number,
						"compacted_length": len(response.content),
						"remaining_items": len(self.state.agent_history_items)
					}
				)
			
			return True
			
		except Exception as e:
			logger.error(f"Message compaction failed: {e}")
			
			# Audit log error
			if self.audit_logger:
				self.audit_logger.log_event(
					AuditEventType.ERROR,
					{
						"action": "message_compaction_failed",
						"step_number": step_info.step_number,
						"error": str(e)
					}
				)
			
			return False

	def _build_compaction_prompt(self, history_items: List[HistoryItem], step_info: AgentStepInfo) -> str:
		"""Build prompt for message compaction"""
		history_text = "\n".join(item.to_string() for item in history_items)
		
		return f"""Please summarize the following automation history into a concise memory block.
Focus on key decisions, outcomes, and important context that should be remembered.

Current step: {step_info.step_number}
Total history items: {len(history_items)}

History:
{history_text}

Provide a concise summary that captures the essential information for continuing the automation task."""

	def get_secret(self, secret_name: str, context: Optional[Dict] = None) -> Optional[str]:
		"""Get secret from secret manager with audit logging"""
		if self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.SECRET_MANAGER_ACCESS,
				{
					"secret_name": secret_name,
					"secret_manager_type": self.secret_manager.secret_manager_type,
					"has_context": context is not None
				}
			)
		
		return self.secret_manager.get_secret(secret_name, context)

	def verify_compliance(self, action: str, data: Any = None) -> bool:
		"""Verify compliance for a given action"""
		if not self.audit_logger:
			return True
		
		# Log compliance check
		self.audit_logger.log_event(
			AuditEventType.COMPLIANCE_CHECK,
			{
				"action": action,
				"frameworks": [f.value for f in self.compliance_frameworks],
				"data_type": type(data).__name__ if data else None
			}
		)
		
		# Basic compliance checks
		if ComplianceFramework.GDPR in self.compliance_frameworks:
			# GDPR: Ensure no personal data is logged without consent
			if data and isinstance(data, str):
				# Check for potential PII patterns
				for pattern in SENSITIVE_PATTERNS:
					if re.search(pattern, data, re.IGNORECASE):
						logger.warning(f"Potential GDPR violation: sensitive data detected in {action}")
						return False
		
		return True

	def export_audit_trail(self, filepath: Optional[str] = None) -> Optional[str]:
		"""Export audit trail for compliance reporting"""
		if not self.audit_logger:
			return None
		
		return self.audit_logger.export_audit_trail(filepath)

	def verify_audit_integrity(self) -> bool:
		"""Verify audit trail integrity"""
		if not self.audit_logger:
			return True
		
		return self.audit_logger.verify_integrity()

	def __del__(self):
		"""Cleanup and final audit logging"""
		if hasattr(self, 'audit_logger') and self.audit_logger:
			self.audit_logger.log_event(
				AuditEventType.AGENT_STOP,
				{
					"total_events": len(self.audit_logger.audit_trail),
					"integrity_verified": self.audit_logger.verify_integrity()
				}
			)
		
		# Clear secret cache
		if hasattr(self, 'secret_manager'):
			self.secret_manager.clear_cache()