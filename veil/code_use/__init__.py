"""Code-use mode - Jupyter notebook-like code execution for browser automation."""

from veil.code_use.namespace import create_namespace
from veil.code_use.notebook_export import export_to_ipynb, session_to_python_script
from veil.code_use.service import CodeAgent
from veil.code_use.views import CodeCell, ExecutionStatus, NotebookSession

__all__ = [
	'CodeAgent',
	'create_namespace',
	'export_to_ipynb',
	'session_to_python_script',
	'CodeCell',
	'ExecutionStatus',
	'NotebookSession',
]
