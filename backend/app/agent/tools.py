import inspect
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
    low = annotation.lower().replace("typing.", "").replace("optional[", "").removesuffix("]")
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


def create_filesystem_tools() -> List[ToolDef]:
    from app.tools.filesystem import (
        tool_ls, tool_read_file, tool_write_file, tool_edit_file,
        tool_glob, tool_grep, tool_execute,
    )
    tools: List[ToolDef] = []
    for func in [tool_ls, tool_read_file, tool_write_file, tool_edit_file, tool_glob, tool_grep, tool_execute]:
        name = func.__name__
        doc = inspect.getdoc(func) or ""
        sig = inspect.signature(func)
        properties: Dict[str, dict] = {}
        required: List[str] = []
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            json_type = "string"
            properties[p.name] = {"type": json_type, "description": f"Parameter {p.name}"}
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        tools.append(ToolDef(
            name=name,
            description=doc,
            parameters={"type": "object", "properties": properties, "required": required},
            fn=func,
        ))
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
    skill_loader: SkillLoader, plugin_loader: PluginLoader, include_filesystem: bool = True
) -> str:
    enabled_skills = skill_loader.get_enabled_skills()
    enabled_plugins = plugin_loader.get_enabled_plugins()

    parts = [
        "You are a helpful AI assistant.",
    ]

    tool_parts = []

    if include_filesystem:
        tool_parts.append(
            "Built-in filesystem tools (for reading/writing/searching local files):\n"
            "   - tool_ls(path) - List directory contents\n"
            "   - tool_read_file(path, offset, limit) - Read file content\n"
            "   - tool_write_file(path, content) - Create a new file\n"
            "   - tool_edit_file(path, old_string, new_string, replace_all) - Edit a file\n"
            "   - tool_glob(pattern) - Find files matching a pattern\n"
            "   - tool_grep(pattern, include, context, count_only, files_only) - Search file contents\n"
            "   - tool_execute(command, timeout, work_dir) - Run a shell command (build/install only)"
        )

    if enabled_skills:
        skills_desc = "\n".join(
            f"   - load_skill_{s.name.replace('-', '_').replace(' ', '_')}() - Load '{s.name}' skill: {s.description}"
            for s in enabled_skills
        )
        tool_parts.append(f"Skill tools (load skill files):\n{skills_desc}")

    if enabled_plugins:
        lines = []
        for p in enabled_plugins:
            for func_name in p.functions:
                if func_name.startswith("tool_") and p.name == "filesystem":
                    continue
                lines.append(f"   - plugin_{p.name}_{func_name}")
        plugins_desc = "\n".join(lines)
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
        "- Only call tools that are directly relevant to the user's request. Do NOT call unrelated tools.",
        "- For document generation, use the docx-generator plugin (plugin_docx-generator_tool_create_docx) for .docx files, or save content using tool_write_file for other formats.",
        "- For keyword web search (查找信息), use plugin_internet-search_tool_internet_search.",
        "- For fetching content from a specific URL (查看某个网站的内容), use plugin_internet-search_tool_extract_urls.",
        "- CRITICAL: tool_execute is ONLY for building/testing projects (npm install, npm run build, etc.). NEVER use tool_execute for curl, wget, ping, or any network/web operations.",
        "- For knowledge base export, use kb-export tools.",
        "- If the user's request doesn't match any tool's purpose, answer directly without calling tools.",
        "",
        "IMPORTANT - Skill loading before code/design tasks:",
        "  When the user asks to create code, web pages, apps, or designs:",
        "  1. FIRST, call the relevant load_skill_*() tool to get specialized instructions and best practices.",
        "  2. Then follow the skill's guidance to write files using tool_write_file.",
        "  3. Only run tool_execute for build/install if the skill instructs you to.",
        "",
        "IMPORTANT - DOCX / PDF / Excel / PPTX document creation:",
        "  When the user asks to create a Word document (.docx):",
        "  - Use the docx-generator plugin (plugin_docx-generator_tool_create_docx).",
        "    sections JSON supports: heading, paragraph, table, bullet_list.",
        "  When the user asks to create a PDF document (.pdf):",
        "  - Use the pdf-generator plugin (plugin_pdf-generator_tool_create_pdf).",
        "    sections JSON supports: heading, paragraph, table, bullet_list, horizontal_rule.",
        "  When the user asks to create an Excel spreadsheet (.xlsx):",
        "  - Use the excel-generator plugin (plugin_excel-generator_tool_create_excel).",
        "    sheets JSON supports: name, headers, rows. Supports multiple sheets.",
        "  When the user asks to create a PowerPoint presentation (.pptx):",
        "  - Use the pptx-generator plugin (plugin_pptx-generator_tool_create_pptx).",
        "    slides JSON supports types: title, section_header, content, two_column, table.",
        "    Each slide supports optional bg_color and font_color.",
        "  Files are saved per the user's specified directory rules.",
        "",
        "IMPORTANT - Order of operations for creating projects:",
        "  1. FIRST, write ALL necessary code files using tool_write_file (it auto-creates directories).",
        "  2. Do NOT use tool_execute with mkdir or Set-Content to create files — use tool_write_file instead.",
        "  3. ONLY AFTER all files are written, run tool_execute for npm install or build if needed.",
        "  Do NOT run 'npm create', 'npx create-react-app', 'npm create vite' etc. Write files manually.",
    ])

    return "\n".join(parts)
