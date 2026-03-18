import os
from typing import TYPE_CHECKING

from veil.logging_config import setup_logging

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
	from veil.config import CONFIG

	# Get log file paths from config/environment
	debug_log_file = getattr(CONFIG, 'BROWSER_USE_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'BROWSER_USE_INFO_LOG_FILE', None)

	# Set up logging with file handlers if specified
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

	logger = logging.getLogger('veil')

# Monkeypatch BaseSubprocessTransport.__del__ to handle closed event loops gracefully
from asyncio import base_subprocess

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""Patched __del__ that handles closed event loops without throwing noisy red-herring errors like RuntimeError: Event loop is closed"""
	try:
		# Check if the event loop is closed before calling the original
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			# Event loop is closed, skip cleanup that requires the loop
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' in str(e):
			# Silently ignore this specific error
			pass
		else:
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del


# Type stubs for lazy imports - fixes linter warnings
if TYPE_CHECKING:
	from veil.agent.prompts import SystemPrompt
	from veil.agent.service import Agent

	# from veil.agent.service import Agent
	from veil.agent.views import ActionModel, ActionResult, AgentHistoryList
	from veil.browser import BrowserProfile, BrowserSession
	from veil.browser import BrowserSession as Browser
	from veil.code_use.service import CodeAgent
	from veil.dom.service import DomService
	from veil.llm import models
	from veil.llm.anthropic.chat import ChatAnthropic
	from veil.llm.azure.chat import ChatAzureOpenAI
	from veil.llm.veil.chat import ChatBrowserUse
	from veil.llm.google.chat import ChatGoogle
	from veil.llm.groq.chat import ChatGroq
	from veil.llm.litellm.chat import ChatLiteLLM
	from veil.llm.mistral.chat import ChatMistral
	from veil.llm.oci_raw.chat import ChatOCIRaw
	from veil.llm.ollama.chat import ChatOllama
	from veil.llm.openai.chat import ChatOpenAI
	from veil.llm.vercel.chat import ChatVercel
	from veil.sandbox import sandbox
	from veil.tools.service import Controller, Tools

	# Lazy imports mapping - only import when actually accessed
_LAZY_IMPORTS = {
	# Agent service (heavy due to dependencies)
	# 'Agent': ('veil.agent.service', 'Agent'),
	# Code-use agent (Jupyter notebook-like execution)
	'CodeAgent': ('veil.code_use.service', 'CodeAgent'),
	'Agent': ('veil.agent.service', 'Agent'),
	# System prompt (moderate weight due to agent.views imports)
	'SystemPrompt': ('veil.agent.prompts', 'SystemPrompt'),
	# Agent views (very heavy - over 1 second!)
	'ActionModel': ('veil.agent.views', 'ActionModel'),
	'ActionResult': ('veil.agent.views', 'ActionResult'),
	'AgentHistoryList': ('veil.agent.views', 'AgentHistoryList'),
	'BrowserSession': ('veil.browser', 'BrowserSession'),
	'Browser': ('veil.browser', 'BrowserSession'),  # Alias for BrowserSession
	'BrowserProfile': ('veil.browser', 'BrowserProfile'),
	# Tools (moderate weight)
	'Tools': ('veil.tools.service', 'Tools'),
	'Controller': ('veil.tools.service', 'Controller'),  # alias
	# DOM service (moderate weight)
	'DomService': ('veil.dom.service', 'DomService'),
	# Chat models (very heavy imports)
	'ChatOpenAI': ('veil.llm.openai.chat', 'ChatOpenAI'),
	'ChatGoogle': ('veil.llm.google.chat', 'ChatGoogle'),
	'ChatAnthropic': ('veil.llm.anthropic.chat', 'ChatAnthropic'),
	'ChatBrowserUse': ('veil.llm.veil.chat', 'ChatBrowserUse'),
	'ChatGroq': ('veil.llm.groq.chat', 'ChatGroq'),
	'ChatLiteLLM': ('veil.llm.litellm.chat', 'ChatLiteLLM'),
	'ChatMistral': ('veil.llm.mistral.chat', 'ChatMistral'),
	'ChatAzureOpenAI': ('veil.llm.azure.chat', 'ChatAzureOpenAI'),
	'ChatOCIRaw': ('veil.llm.oci_raw.chat', 'ChatOCIRaw'),
	'ChatOllama': ('veil.llm.ollama.chat', 'ChatOllama'),
	'ChatVercel': ('veil.llm.vercel.chat', 'ChatVercel'),
	# LLM models module
	'models': ('veil.llm.models', None),
	# Sandbox execution
	'sandbox': ('veil.sandbox', 'sandbox'),
}


def __getattr__(name: str):
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			if attr_name is None:
				# For modules like 'models', return the module itself
				attr = module
			else:
				attr = getattr(module, attr_name)
			# Cache the imported attribute in the module's globals
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	'Agent',
	'CodeAgent',
	# 'CodeAgent',
	'BrowserSession',
	'Browser',  # Alias for BrowserSession
	'BrowserProfile',
	'Controller',
	'DomService',
	'SystemPrompt',
	'ActionResult',
	'ActionModel',
	'AgentHistoryList',
	# Chat models
	'ChatOpenAI',
	'ChatGoogle',
	'ChatAnthropic',
	'ChatBrowserUse',
	'ChatGroq',
	'ChatLiteLLM',
	'ChatMistral',
	'ChatAzureOpenAI',
	'ChatOCIRaw',
	'ChatOllama',
	'ChatVercel',
	'Tools',
	'Controller',
	# LLM models module
	'models',
	# Sandbox execution
	'sandbox',
]
