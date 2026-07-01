from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader


_TYPE_MAP: Dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
    "Any": "string",
    "optional": "string",
}


def _annotation_to_json_type(annotation: str) -> str:
    low = annotation.lower().replace("typing.", "").replace("optional[", "").rstrip("]")
    for k, v in _TYPE_MAP.items():
        if k in low:
            return v
    return "string"


def _build_parameters_schema(params: List[dict]) -> dict:
    properties: Dict[str, dict] = {}
    required: List[str] = []
    for p in params:
        json_type = _annotation_to_json_type(p["annotation"])
        prop: dict = {"type": json_type, "description": f"Parameter {p['name']}"}
        properties[p["name"]] = prop
        if p["default"] is None:
            required.append(p["name"])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    fn: Callable

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def create_skill_tools(skill_loader: SkillLoader) -> List[ToolDef]:
    tools: List[ToolDef] = []
    for skill in skill_loader.get_enabled_skills():
        content = skill_loader.get_skill_content(skill.name)
        name = f"load_skill_{skill.name.replace('-', '_').replace(' ', '_')}"
        description = f"Load the '{skill.name}' skill content. Description: {skill.description}"

        def make_skill_fn(n: str, desc: str, c: str) -> ToolDef:
            def skill_tool() -> str:
                return c

            skill_tool.__name__ = n
            skill_tool.__doc__ = desc
            return ToolDef(
                name=n,
                description=desc,
                parameters={"type": "object", "properties": {}, "required": []},
                fn=skill_tool,
            )

        tools.append(make_skill_fn(name, description, content or ""))
    return tools


def create_plugin_tools(plugin_loader: PluginLoader) -> List[ToolDef]:
    tools: List[ToolDef] = []
    for plugin in plugin_loader.get_enabled_plugins():
        for func_name, func in plugin.functions.items():
            meta = plugin.functions_meta.get(func_name, {})
            params = meta.get("params", [])
            desc = meta.get("description", func.__doc__ or f"Call {func_name} from {plugin.name} plugin")

            def make_plugin_tool(
                pl: PluginLoader, p_name: str, f_name: str, fn: Callable, param_schema: dict
            ) -> ToolDef:
                def plugin_tool(**kwargs) -> Any:
                    return pl.call_function(p_name, f_name, **kwargs)

                plugin_tool.__name__ = f"plugin_{p_name}_{f_name}"
                plugin_tool.__doc__ = desc
                return ToolDef(
                    name=f"plugin_{p_name}_{f_name}",
                    description=desc,
                    parameters=param_schema,
                    fn=plugin_tool,
                )

            tools.append(make_plugin_tool(
                plugin_loader, plugin.name, func_name, func, _build_parameters_schema(params)
            ))
    return tools


def build_system_prompt_no_kb(
    skill_loader: SkillLoader, plugin_loader: PluginLoader
) -> str:
    enabled_skills = skill_loader.get_enabled_skills()
    enabled_plugins = plugin_loader.get_enabled_plugins()

    parts = [
        "You are a helpful AI assistant.",
    ]

    tool_parts = []
    if enabled_skills:
        skills_desc = "\n".join(
            f"   - load_skill_{s.name.replace('-', '_').replace(' ', '_')}() - Load '{s.name}' skill: {s.description}"
            for s in enabled_skills
        )
        tool_parts.append(f"Skill tools (load skill files):\n{skills_desc}")

    if enabled_plugins:
        plugins_desc = "\n".join(
            f"   - {tool_name}" for p in enabled_plugins for tool_name in p.functions
        )
        tool_parts.append(f"Plugin tools:\n{plugins_desc}")

    if tool_parts:
        parts.append("You can use the following tools:")
        parts.extend(tool_parts)

    parts.extend([
        "",
        "Instructions:",
        "- There is no knowledge base available (no documents uploaded).",
        "- Answer based on your own knowledge.",
        "- If you don't know something, say so honestly.",
    ])

    return "\n".join(parts)
