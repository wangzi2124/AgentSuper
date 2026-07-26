"""Context management modules.

Provides token counting, tool output bounding, deduplication,
context compaction, task state persistence, and the task runner.
"""

from app.context.token_counter import estimate_tokens, count_message_tokens, truncate_messages
from app.context.tool_output import bound_tool_output, ToolOutputLimits
from app.context.tool_dedup import ToolResultDedup
from app.context.compaction import ContextCompactor
from app.context.task_state import TaskState
from app.context.task_runner import TaskRunner

__all__ = [
    # Token counting
    "estimate_tokens",
    "count_message_tokens",
    "truncate_messages",
    # Tool output
    "bound_tool_output",
    "ToolOutputLimits",
    # Dedup
    "ToolResultDedup",
    # Compaction
    "ContextCompactor",
    # Task state
    "TaskState",
    # Task runner
    "TaskRunner",
]
