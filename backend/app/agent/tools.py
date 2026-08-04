import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader


def _writable_workspaces() -> list[str]:
    """返回当前所有可写工作区的绝对路径（主工作区 + 运行时/配置的额外工作区）。"""
    try:
        from app.permission import get_manager
        return get_manager().list_workspaces()
    except Exception:
        return []


# Python类型到JSON Schema类型的映射表（精确匹配外层类型）
_TYPE_MAP: Dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "dict": "object",
    "mapping": "object",
    "list": "array",
    "sequence": "array",
    "any": "string",
}


def _annotation_to_json_type(annotation: str) -> str:
    """将Python类型注解转换为JSON Schema类型。

    先剥离 Optional/Union 外壳取内层类型，再按外层容器（List/Dict）精确匹配，
    避免子串匹配导致的 `List[str]` → string 等错误。
    """
    low = annotation.lower().replace("typing.", "").strip()
    if low.startswith("optional["):
        low = low[len("optional["):-1]
    if low.startswith("list[") or low.startswith("tuple[") or low.startswith("sequence["):
        return "array"
    if low.startswith("dict[") or low.startswith("mapping[") or low.startswith("dict["):
        return "object"
    for k, v in _TYPE_MAP.items():
        if low == k or low == f"<class '{k}'>":
            return v
    return "string"


def _build_parameters_schema(params: List[dict]) -> dict:
    """根据参数列表构建OpenAI工具的参数Schema。"""
    properties: Dict[str, dict] = {}
    required: List[str] = []
    for p in params:
        json_type = _annotation_to_json_type(p["annotation"])
        prop: dict = {"type": json_type, "description": f"Parameter {p['name']}"}
        properties[p["name"]] = prop
        # 有默认值的参数（含默认值为 None 的可选参数）不标为必填
        if not p.get("has_default", p["default"] is not None):
            required.append(p["name"])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


@dataclass
class ToolDef:
    """工具定义数据类，包含工具的名称、描述、参数和执行函数。"""
    name: str
    description: str
    parameters: dict
    fn: Callable

    def to_openai_tool(self) -> dict:
        """将工具定义转换为OpenAI函数调用格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def create_skill_tools(skill_loader: SkillLoader) -> List[ToolDef]:
    """根据技能加载器创建技能工具列表。"""
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
    """创建文件系统操作工具列表（ls、read、write、append、edit等）。"""
    from app.tools.filesystem import (
        tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file,
        tool_glob, tool_grep, tool_execute, tool_delete_file, tool_rename_file,
    )
    tools: List[ToolDef] = []

    # Manual parameter descriptions for each tool
    _PARAM_DOCS: dict[str, dict[str, str]] = {
        "tool_ls": {"path": "Directory path to list (default: current directory)"},
        "tool_read_file": {"path": "File path to read", "offset": "Line number to start reading from (1-indexed, default: 1)", "limit": "Maximum number of lines to read (0 = all)"},
        "tool_write_file": {"path": "File path to create", "content": "Text content to write into the file", "overwrite": "If true, overwrite existing file (default: false)"},
        "tool_append_file": {"path": "File path to append to (created if missing)", "content": "Text content to append to the end of the file"},
        "tool_edit_file": {"path": "File path to edit", "old_string": "Text to search for and replace", "new_string": "Replacement text", "replace_all": "If true, replace ALL occurrences; if false, replace only the first (default: false)"},
        "tool_glob": {"pattern": "Glob pattern to match files (e.g. **/*.py)", "root": "Directory to search in (default: workspace; absolute paths allowed, e.g. F:\\tetris)"},
        "tool_grep": {"pattern": "Regex pattern to search for", "include": "File glob pattern to restrict search (e.g. *.py)", "context": "Number of context lines before/after each match", "count_only": "If true, return only match counts per file", "files_only": "If true, return only file paths", "root": "Directory to search in (default: workspace; absolute paths allowed, e.g. F:\\tetris)"},
        "tool_execute": {"command": "Shell command to run", "timeout": "Max execution time in seconds (default 300, max 600)", "work_dir": "Working directory for the command (default: current directory)"},
        "tool_delete_file": {"path": "File or empty directory path to delete"},
        "tool_rename_file": {"path": "Source path to rename/move", "new_path": "Destination path"},
    }

    for func in [tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file, tool_glob, tool_grep, tool_execute, tool_delete_file, tool_rename_file]:
        name = func.__name__
        sig = inspect.signature(func)
        param_docs = _PARAM_DOCS.get(name, {})
        properties: Dict[str, dict] = {}
        required: List[str] = []
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            annotation = p.annotation
            ann_name = getattr(annotation, "__name__", None) or "string"
            json_type = _annotation_to_json_type(ann_name)
            desc = param_docs.get(p.name, f"Parameter {p.name}")
            properties[p.name] = {"type": json_type, "description": desc}
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
        # Use a concise description for the tool
        _DESC = {
            "tool_ls": "List files and directories",
            "tool_read_file": "Read file content (text or base64 for images/pdf/audio/video)",
            "tool_write_file": "Create a new file with text content (auto-creates parent directories)",
            "tool_append_file": "Append text content to a file (creates it if missing). Use for large files: write the first chunk then append in chunks.",
            "tool_edit_file": "Edit a file by replacing text (single or all occurrences)",
            "tool_glob": "Find files matching a glob pattern",
            "tool_grep": "Search file contents using regex",
            "tool_execute": "Run a shell command (build/install/test only, NOT for network/web operations)",
            "tool_delete_file": "Delete a file or empty directory (workspace only)",
            "tool_rename_file": "Rename or move a file/directory",
        }
        tools.append(ToolDef(
            name=name,
            description=_DESC.get(name, name),
            parameters={"type": "object", "properties": properties, "required": required},
            fn=func,
        ))
    return tools


def create_plugin_tools(plugin_loader: PluginLoader) -> List[ToolDef]:
    """根据插件加载器创建插件工具列表。"""
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
    """构建无知识库时的系统提示词，包含可用工具说明。"""
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
            "   - tool_write_file(path, content, overwrite) - Create a new file\n"
            "   - tool_append_file(path, content) - Append content to a file (creates if missing)\n"
            "   - tool_edit_file(path, old_string, new_string, replace_all) - Edit a file\n"
            "   - tool_glob(pattern, root) - Find files matching a pattern (root defaults to workspace)\n"
            "   - tool_grep(pattern, include, context, count_only, files_only, root) - Search file contents\n"
            "   - tool_execute(command, timeout, work_dir) - Run a shell command (build/install only)\n"
            "   - tool_delete_file(path) - Delete a file or empty directory\n"
            "   - tool_rename_file(path, new_path) - Rename or move a file/directory"
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
        "- For web search (查找信息/搜索), use plugin_internet-search_tool_internet_search.",
        "  - Use region='cn' for Chinese content, region='global' for international.",
        "  - Use engine='auto' to auto-select. Engines: tavily (requires TAVILY_API_KEY), bing (requires BING_API_KEY), duckduckgo (free). Avoid 'baidu' (anti-bot, usually returns nothing).",
        "- For fetching content from a specific URL (查看某个网站的内容), use plugin_internet-search_tool_extract_urls.",
        "- For HTTP requests (testing APIs, calling endpoints, fetching data from URLs), use plugin_http-client_tool_http_request, plugin_http-client_tool_http_get, or plugin_http-client_tool_http_post.",
        "  - Pass headers as a JSON string, e.g. {\"Authorization\": \"Bearer xxx\"}.",
        "  - Pass body as a JSON string for JSON requests, or key=value&key2=value2 for form data.",
        "  - For simple GET requests, prefer plugin_http-client_tool_http_get.",
        "  - For simple POST with JSON body, prefer plugin_http-client_tool_http_post.",
        "- CRITICAL: tool_execute is ONLY for building/testing projects (npm install, npm run build, etc.). NEVER use tool_execute for curl, wget, ping, or any network/web operations. Use the http-client plugin instead.",
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
        "",
        "IMPORTANT - Writing large files:",
        "  A single tool call cannot carry very large content (LLM output is limited). For content larger than",
        "  roughly 6KB (about 150 lines), do NOT try to write it all at once:",
        "  1. Use tool_write_file(path, content, overwrite=True) to write the first chunk.",
        "  2. Then append the remaining chunks with tool_append_file(path, content), one chunk per call.",
        "  This avoids truncated/corrupted files. You may also use tool_read_file to verify after writing.",
        "",
        "IMPORTANT - Working paths / workspace:",
        "  The following absolute paths are writable workspaces (you MUST write files under one of them):",
        *[f"    - {w}" for w in _writable_workspaces()],
        "  Relative paths resolve under the first workspace above. If the user asks to write to a path",
        "  NOT in this list, you will get a Permission denied error telling you the reason — do NOT",
        "  blindly retry; instead report it, or ask the user to add the path to the workspace list in",
        "  the UI (workspace list updates take effect immediately).",
        "  To search files in a configured workspace, pass root=<absolute path> to tool_glob / tool_grep.",
        "  tool_grep / tool_glob return absolute paths when searching a custom root.",
        "",
        "IMPORTANT - Planning & final report for multi-step tasks:",
        "  When the task needs multiple steps or multiple files (e.g. building a project):",
        "  1. FIRST output a short plan block marked '## 实施计划' listing the steps as a checklist.",
        "  2. As you work, keep the plan visible and mark each step's progress.",
        "  3. ALWAYS end your final answer with a '## 完成情况' section listing:",
        "     - 已完成 (what was completed)",
        "     - 未完成 (what was NOT completed, if any)",
        "     - 下一步 (concrete next steps to finish the task)",
        "  4. When the step limit is reached, you MUST give this report without calling more tools.",
    ])

    return "\n".join(parts)
