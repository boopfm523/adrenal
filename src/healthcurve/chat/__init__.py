"""Private owner-scoped conversational analysis.

The package deliberately exposes read tools separately from Ollama orchestration. A
model can request a named tool, but never receives a SQL connection, owner identifier,
or mutation operation (ADR-0025).
"""

from healthcurve.chat.tools import (
    CHAT_TOOL_CATALOG_VERSION,
    ChatToolError,
    ChatToolResult,
    execute_chat_tool,
    tool_definitions,
)

__all__ = [
    "CHAT_TOOL_CATALOG_VERSION",
    "ChatToolError",
    "ChatToolResult",
    "execute_chat_tool",
    "tool_definitions",
]
