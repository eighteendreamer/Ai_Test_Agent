"""
DOM mode package.

DOM is the primary execution path. Vision models are kept only as an
auxiliary fallback when DOM-driven execution cannot proceed.
"""

from .detector import DomRichnessDetector
from .agent_browser_client import AgentBrowserClient
from .dom_executor import DomExecutor

__all__ = ["DomRichnessDetector", "AgentBrowserClient", "DomExecutor"]
