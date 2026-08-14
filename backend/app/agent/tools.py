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


# 长内容必须写文件、不得在回复中粘贴全文的硬性规则（对齐 opencode 长任务机制：
# 长文用工具写入文件、回复只留摘要，避免单轮输出触发 finish_reason="length" 截断）。
LONG_CONTENT_FILE_RULE = (
    "IMPORTANT - Long content MUST be written to files, NOT pasted in replies:\n"
    "  If your reply would exceed roughly 500 Chinese characters (about 1000 tokens), "
    "you MUST write the full content to a file instead of outputting it inline:\n"
    "  1. Text or code -> tool_write_file(path, content, overwrite=True). For very large files, "
    "write the first chunk with tool_write_file then append the rest with tool_append_file.\n"
    "  2. Structured documents (.docx / .pdf / .xlsx / .pptx) -> use the corresponding generator plugin.\n"
    "  3. In your reply, only output: the saved file path + a summary of the key points + structure overview. "
    "Do NOT paste the full content.\n"
    "  4. Do NOT add closing remarks asking the user to verify the file, e.g. never write "
    "'文档已生成完毕，请验证文件完整性和内容结构' or similar verification notices. "
    "Simply state the saved path and summary, then stop.\n"
    "  Exception: ONLY if the user explicitly asks for the full text inline."
)


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
        # [token 优化 v3] 描述截断到 200 字符：40 个技能全启用时避免 schema 体积膨胀（完整描述仍在 SKILL.md）
        _d = " ".join(skill.description.split())
        description = f"Load the '{skill.name}' skill content. Description: {_d[:200]}{'…' if len(_d) > 200 else ''}"

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
    from app.tools.file_tools import (
        tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file,
        tool_glob, tool_grep, tool_execute, tool_delete_file, tool_rename_file,
    )
    tools: List[ToolDef] = []

    # Manual parameter descriptions for each tool
    _PARAM_DOCS: dict[str, dict[str, str]] = {
        "tool_ls": {"path": "Directory path to list (default: current directory)"},
        "tool_read_file": {"path": "File path to read", "offset": "Line number to start reading from (1-indexed, default: 1)", "limit": "Maximum number of lines to read (default: 2000; 0 also means 2000; for large files paginate via offset)"},
        "tool_write_file": {"path": "File path to create", "content": "Text content to write into the file", "overwrite": "If true, overwrite existing file (default: false)"},
        "tool_append_file": {"path": "File path to append to (created if missing)", "content": "Text content to append to the end of the file"},
        "tool_edit_file": {"path": "File path to edit", "old_string": "Text to search for and replace", "new_string": "Replacement text", "replace_all": "If true, replace ALL occurrences; if false, replace only the first. Multiple matches without replace_all cause an error (default: false)"},
        "tool_glob": {"pattern": "Glob pattern to match files (e.g. **/*.py)", "root": "Directory to search in (default: workspace; absolute paths allowed, e.g. F:\\tetris)"},
        "tool_grep": {"pattern": "Regex pattern to search for", "include": "File glob pattern to restrict search (e.g. *.py)", "context": "Number of context lines before/after each match", "count_only": "If true, return only match counts per file", "files_only": "If true, return only file paths", "root": "Directory to search in (default: workspace; absolute paths allowed, e.g. F:\\tetris)"},
        "tool_execute": {"command": "Shell command to run (supports pipes/redirects/&&; every command segment is whitelist-checked)", "timeout": "Max execution time in seconds (default 300, max 600)", "work_dir": "Working directory for the command (default: current directory)"},
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
            "tool_read_file": "Read file content (text with line numbers; max 2000 lines / 50KB per call, paginate via offset; base64 for images/pdf/audio/video)",
            "tool_write_file": "Create a new file with text content (auto-creates parent directories)",
            "tool_append_file": "Append text content to a file (creates it if missing). Use for large files: write the first chunk then append in chunks.",
            "tool_edit_file": "Edit a file by replacing text (fuzzy matching; errors when multiple matches unless replace_all)",
            "tool_glob": "Find files matching a glob pattern (mtime-sorted, max 100)",
            "tool_grep": "Search file contents using regex (max 100 matches)",
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
    skill_loader: SkillLoader, plugin_loader: PluginLoader, include_filesystem: bool = True, cwd: str = ""
) -> str:
    """构建无知识库时的系统提示词，包含可用工具说明。

    cwd: 当前会话绑定的工作目录（opencode ctx.directory）。非空时写入提示词，
    告知 LLM 相对路径以该目录为基准解析。
    """
    enabled_skills = skill_loader.get_enabled_skills()
    enabled_plugins = plugin_loader.get_enabled_plugins()

    parts = [
        "You are a helpful AI assistant.",
    ]

    tool_parts = []

    if include_filesystem:
        tool_parts.append(
            "Built-in filesystem tools (for reading/writing/searching local files):\n"
            "   - tool_ls(path) - List directory contents (gitignored items omitted)\n"
            "   - tool_read_file(path, offset, limit) - Read file content (default 2000 lines, max 50KB; paginate via offset)\n"
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
        # [token 优化 v10] 不再逐一列出全部技能名（30+ 技能约 1.3K 字符，固定随每次调用发出）。
        # 技能清单 + 截断描述已由 graph._build_tool_defs 按意图把 load_skill_* schema 按需挂载，
        # 系统提示词只保留一行提示，使前缀保持完全静态，最大化 DeepSeek 前缀缓存命中。
        tool_parts.append(
            "Skill tools (load_skill_<name>()): specialized skills are available. The inventory and "
            "descriptions of relevant skills are mounted into the tool schema based on your request — "
            "call the matching load_skill_<name>() tool when the task calls for one (documents, "
            "web/frontend, design, coding practices, teaching, research, etc.)."
        )

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
        "- No knowledge base available; answer from your own knowledge; say so honestly if unsure.",
        "- Only call tools directly relevant to the request.",
        "- Docs: use docx/pdf/excel/pptx generator plugins, or tool_write_file for other formats.",
        "- Web search: plugin_internet-search_tool_internet_search (region='cn'|'global').",
        "- URL content: plugin_internet-search_tool_extract_urls.",
        "- HTTP: plugin_http-client_tool_http_get/_post/_request (headers as JSON string).",
        "- CRITICAL: tool_execute ONLY for build/install (npm install, npm run build). NEVER for curl/wget/ping/network — use http-client plugin.",
        "- KB export: kb-export tools.",
        "- If no tool fits, answer directly without calling tools.",
        "",
        "IMPORTANT - Before code/design tasks: FIRST call the relevant load_skill_*() tool for best practices, "
        "then write files with tool_write_file; tool_execute only for build/install if the skill says so.",
        "",
        "IMPORTANT - Documents (.docx/.pdf/.xlsx/.pptx): use the matching generator plugin "
        "(docx/pdf/excel/pptx-generator); section/slide schemas are in each tool's description. Files saved per your directory rules.",
        "",
        "IMPORTANT - Projects: write ALL code files first via tool_write_file (auto-creates dirs); do NOT use "
        "mkdir/Set-Content or 'npm create'/'create-react-app'/'create vite'; only then run tool_execute for install/build.",
        "",
        "IMPORTANT - Long content MUST be written to files, NOT pasted in replies (≈500 Chinese chars / 1000 tokens):",
        "  Write text/code with tool_write_file (first chunk, then tool_append_file for large files); generator plugins for .docx/.pdf/.xlsx/.pptx.",
        "  Reply with only: saved path + summary + structure. No 'please verify' closing remarks.",
        "",
        "IMPORTANT - Working paths / workspace:",
        "  The following absolute paths are writable workspaces (you MUST write files under one of them):",
        *[f"    - {cwd}  (current session working directory)" for cwd in [cwd] if cwd],
        *[f"    - {w}" for w in _writable_workspaces()],
        "  Relative paths resolve under the current session working directory above (if set), "
        "otherwise under the first workspace above. If the user asks to write to a path",
        "  NOT in this list, you will get a Permission denied error telling you the reason — do NOT",
        "  blindly retry; instead report it, or ask the user to add the path to the workspace list in",
        "  the UI (workspace list updates take effect immediately).",
        "  To search files in a configured workspace, pass root=<absolute path> to tool_glob / tool_grep.",
        "  tool_grep / tool_glob return absolute paths when searching a custom root.",
        "",
        "IMPORTANT - Multi-step tasks: start with plan block '## 实施计划'; end with '## 完成情况' "
        "(已完成 / 未完成 / 下一步). At step limit, give this report without calling more tools.",
    ])

    return "\n".join(parts)
