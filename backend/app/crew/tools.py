"""Bridge existing plugins and filesystem tools to CrewAI tools."""

import logging
from typing import Any, List

from crewai.tools import BaseTool as CrewAIBaseTool

from app.agent.tools import ToolDef, create_filesystem_tools, create_skill_tools, create_plugin_tools
from app.plugins.loader import PluginLoader
from app.skills.loader import SkillLoader

logger = logging.getLogger(__name__)


class _ToolDefAdapter(CrewAIBaseTool):
    """Wrap a ToolDef into a CrewAI tool."""
    name: str = "adapter_tool"
    description: str = ""
    _tool_fn: Any = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, tool_def: ToolDef, **kwargs):
        super().__init__(name=tool_def.name, description=tool_def.description, **kwargs)
        self._tool_fn = tool_def.fn

    def _run(self, **kwargs) -> str:
        try:
            result = self._tool_fn(**kwargs)
            return str(result)
        except Exception as e:
            return f"Error: {e}"


def create_crewai_tools(
    plugin_loader: PluginLoader | None = None,
    skill_loader: SkillLoader | None = None,
    include_filesystem: bool = True,
) -> List[CrewAIBaseTool]:
    """Create CrewAI tools from all available sources.

    Bridges filesystem tools, skill tools, and plugin tools
    into CrewAI-compatible tool objects.
    """
    tool_defs: List[ToolDef] = []

    if include_filesystem:
        tool_defs.extend(create_filesystem_tools())

    if skill_loader:
        tool_defs.extend(create_skill_tools(skill_loader))

    if plugin_loader:
        tool_defs.extend(create_plugin_tools(plugin_loader))

    crew_tools = []
    for td in tool_defs:
        crew_tools.append(_ToolDefAdapter(tool_def=td))

    logger.info("Created %d CrewAI tools from plugins/skills/filesystem", len(crew_tools))
    return crew_tools
