"""Context management modules.

Provides token counting, tool output bounding, deduplication,
context compaction, and task state persistence.
"""

from app.context.token_counter import estimate_tokens, count_message_tokens, truncate_messages
from app.context.tool_output import bound_tool_output, ToolOutputLimits, prune_tool_outputs
from app.context.tool_dedup import ToolResultDedup
from app.context.compaction import ContextCompactor, previous_summary_of
from app.context.task_state import TaskState
from app.context.budget import usable_context_tokens, compaction_threshold_tokens

__all__ = [
    # Token counting
    "estimate_tokens",
    "count_message_tokens",
    "truncate_messages",
    # Budget
    "usable_context_tokens",
    "compaction_threshold_tokens",
    # Tool output
    "bound_tool_output",
    "ToolOutputLimits",
    "prune_tool_outputs",
    # Dedup
    "ToolResultDedup",
    # Compaction
    "ContextCompactor",
    "previous_summary_of",
    # Task state
    "TaskState",
]
