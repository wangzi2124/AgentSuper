"""Bridge existing plugins and filesystem tools to CrewAI tools."""

import logging
from typing import Any, List, Optional

from crewai.tools import BaseTool as CrewAIBaseTool
from pydantic import BaseModel, Field, create_model

from app.agent.tools import ToolDef, create_filesystem_tools, create_skill_tools, create_plugin_tools
from app.plugins.loader import PluginLoader
from app.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

# JSON Schema type → Python type mapping
_JSON_TYPE_MAP = {
    "string": (str, ...),
    "integer": (int, ...),
    "number": (float, ...),
    "boolean": (bool, ...),
    "object": (dict, ...),
    "array": (list, ...),
}


def _build_args_schema(name: str, parameters: dict) -> Optional[type[BaseModel]]:
    """Build a Pydantic model from a ToolDef's JSON Schema parameters."""
    properties = parameters.get("properties", {})
    if not properties:
        return None
    required = set(parameters.get("required", []))

    fields = {}
    for field_name, prop in properties.items():
        json_type = prop.get("type", "string")
        py_type, default = _JSON_TYPE_MAP.get(json_type, (str, ...))
        description = prop.get("description", "")
        if field_name not in required:
            py_type = Optional[py_type]
            default = None
        fields[field_name] = (py_type, Field(default, description=description))

    return create_model(f"{name}_args", **fields)


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
        schema = _build_args_schema(tool_def.name, tool_def.parameters)
        if schema:
            self.args_schema = schema

    def _run(self, **kwargs) -> str:
        try:
            # Coerce string → int / bool for known numeric params
            # (LLM/CrewAI may pass numbers as strings in JSON)
            import inspect
            sig = inspect.signature(self._tool_fn)
            for p_name, p_param in sig.parameters.items():
                if p_name not in kwargs:
                    continue
                val = kwargs[p_name]
                p_anno = p_param.annotation
                if p_anno is int and isinstance(val, str):
                    try:
                        kwargs[p_name] = int(val)
                    except (ValueError, TypeError):
                        pass
                elif p_anno is bool and isinstance(val, str):
                    kwargs[p_name] = val.lower() in ("true", "1", "yes")
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
