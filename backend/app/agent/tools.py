import inspect
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.plugins.loader import PluginLoader
from app.skills.loader import SkillLoader


def _writable_workspaces() -> list[str]:
    """返回当前所有可写工作区的绝对路径（主工作区 + 运行时/配置的额外工作区）。"""
    try:
        from app.permission import get_manager
        return get_manager().list_workspaces()
    except Exception:
        return []


# [P0.2 提示词瘦身] 文件/制品任务的三组输出规则（长内容↔文件、一轮批量写、实施计划/完成情况报告）
# 已迁到内置技能 artifact-writing（app/skills/bundled/productivity/artifact-writing/SKILL.md）：
# 规则正文按需注入，不再每轮占据 system 预算、稀释其余段落的注意力。系统提示只保留下面这一行触发指引，
# 表意与旧 LONG_CONTENT_FILE_RULE + BATCH_WRITES_RULE + 计划/报告段完全一致（防截断 / 防超时 / 报告格式）。
ARTIFACT_SKILL_HINT = (
    "IMPORTANT - File/artifact tasks (writing code, documents, reports, scripts, projects, or any multi-step "
    "task that creates/modifies files): FIRST call load_skill_artifact_writing to load the required output rules "
    "(long content -> file, NOT inline; batch ALL file writes into ONE turn; plan '## 实施计划' / report "
    "'## 完成情况'). For plain Q&A, skip this and answer directly."
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
        # [opencode 对齐] disable-model-invocation：该技能仅由用户显式触发，
        # 不生成 load_skill_* 工具（弱模型会误调用并胡言乱语，如 to-spec/setup-*）。
        if getattr(skill, "disable_model_invocation", False):
            continue
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
        tool_apply_patch,
    )
    tools: List[ToolDef] = []

    # Manual parameter descriptions for each tool
    # [token 优化 v16] 描述精简：参数说明/工具描述按「无语义损失」原则压缩（评估报告
    # 2026-10-09：文件系统 schema 1618 tok，长描述是可压缩项）。仅删冗余措辞与示例，
    # 保留语义、默认值与边界条件。
    _PARAM_DOCS: dict[str, dict[str, str]] = {
        "tool_ls": {"path": "Directory (default: cwd)"},
        "tool_read_file": {"path": "File path", "offset": "Start line (1-based, default 1)", "limit": "Max lines (default 2000, max 50KB; paginate via offset)"},
        "tool_write_file": {"path": "File path (parent dirs auto-created)", "content": "Text to write", "overwrite": "Overwrite if exists (default true)"},
        "tool_append_file": {"path": "File path (created if missing)", "content": "Text to append"},
        "tool_edit_file": {"path": "File path", "old_string": "Text to replace", "new_string": "Replacement", "replace_all": "Replace all (default false; multiple matches without this error)"},
        "tool_glob": {"pattern": "Glob (e.g. **/*.py)", "path": "Dir (default workspace; absolute allowed)"},
        "tool_grep": {"pattern": "Regex", "include": "File glob filter (e.g. *.py)", "context": "Context lines around match", "count_only": "Return counts only", "files_only": "Return file paths only", "path": "Dir (default workspace; absolute allowed)"},
        "tool_execute": {"command": "Shell command (pipes/redirects/&& ok; each segment whitelist-checked)", "timeout": "Seconds (default 300, max 600)", "workdir": "Working dir (default cwd)"},
        "tool_delete_file": {"path": "File or empty dir to delete"},
        "tool_rename_file": {"path": "Source path", "new_path": "Destination path"},
        "tool_apply_patch": {"patch_text": "apply_patch text: '*** Begin Patch' / '*** Add File: <p>' + '+' lines / '*** Update File: <p>' -/+/space lines / '*** Delete File: <p>' / '*** End Patch'"},
    }

    for func in [tool_ls, tool_read_file, tool_write_file, tool_append_file, tool_edit_file, tool_glob, tool_grep, tool_execute, tool_delete_file, tool_rename_file, tool_apply_patch]:
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
            "tool_read_file": "Read file content (line-numbered; max 2000 lines/50KB, paginate via offset; base64 for images/pdf/audio/video; also lists dirs)",
            "tool_write_file": "Create a file with text (parent dirs auto-created)",
            "tool_append_file": "Append text (creates if missing); for large files write first chunk then append in chunks",
            "tool_edit_file": "Replace text in a file (fuzzy match; errors on multiple matches unless replace_all)",
            "tool_glob": "Find files by glob (absolute paths, mtime-sorted, max 100)",
            "tool_grep": "Search file contents by regex (absolute paths, 'Found N matches', max 100)",
            "tool_execute": "Run a shell command (build/install/test/check only; NOT network/web)",
            "tool_delete_file": "Delete a file or empty dir (workspace only)",
            "tool_rename_file": "Rename or move a file/dir",
            "tool_apply_patch": "Apply apply_patch text (Add/Update/Delete File); best for multiple files in one call",
        }
        # [P0.2] Shell dialect moved out of the system prompt into the tool schema —
        # the model already reads tool_execute's description, so this costs no system budget.
        if sys.platform.startswith("win"):
            _DESC["tool_execute"] += (
                ". Runs via cmd.exe (Windows), NOT bash: use backslash paths for executables "
                "(.venv\\Scripts\\python.exe — forward slashes fail), %ERRORLEVEL% instead of $?, "
                "and & / && instead of ; as separator"
            )
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
    skill_loader: SkillLoader, plugin_loader: PluginLoader, include_filesystem: bool = True, cwd: str = "",
    has_memory: bool = False, has_voice: bool = False,
) -> str:
    """构建无知识库时的系统提示词，包含可用工具说明。

    cwd: 当前会话绑定的工作目录（opencode ctx.directory）。非空时写入提示词，
    告知 LLM 相对路径以该目录为基准解析。
    has_memory: 是否启用共享记忆工具（tool_memory_set/get/search）。注入共享
    记忆管理器时开启（opencode memory 语义）。
    """
    enabled_skills = skill_loader.get_enabled_skills()
    enabled_plugins = plugin_loader.get_enabled_plugins()

    # [CodeAct §10] 仅当受控执行器开启时注入代码执行提示（默认关闭）。
    try:
        from app.config import settings as _settings_codeact
        _codeact_enabled = bool(getattr(_settings_codeact, "codeact_enabled", False))
    except Exception:  # noqa: BLE001
        _codeact_enabled = False

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
            "   - tool_glob(pattern, path) - Find files matching a pattern (path defaults to workspace)\n"
            "   - tool_grep(pattern, include, context, count_only, files_only, path) - Search file contents\n"
            "   - tool_execute(command, timeout, workdir) - Run a shell command (build/install/test/check, e.g. npm install, npm run build, python -m py_compile)\n"
            "   - tool_delete_file(path) - Delete a file or empty directory\n"
            "   - tool_rename_file(path, new_path) - Rename or move a file/directory\n"
            "   - tool_apply_patch(patch_text) - Apply an apply-patch-format patch (Add/Update/Delete File)"
        )
        tool_parts.append(
            "   - tool_task(description, prompt, subagent_type) - Delegate a focused, independent subtask "
            "to a sub-agent ('explore' for read-only codebase exploration, 'plan' for producing a structured "
            "plan) and get its final result back. The sub-agent starts with fresh context — include all details. "
            "Use it for specialized or parallel work; do NOT delegate what you can do directly.\n"
            "     IMPORTANT: sub-agents CANNOT create/edit/delete files or run commands — 'explore' is "
            "read-only and 'plan' only produces a plan. Any task that asks you to create, implement, write or "
            "build something (code, files, projects, configs) MUST be executed by YOU with tool_write_file / "
            "tool_append_file / tool_apply_patch / tool_execute. Delegating such a task can never finish it."
        )

    if has_memory:
        tool_parts.append(
            "Memory tools (session-scoped shared memory, aligned with opencode memory):\n"
            "   - tool_memory_set(key, value, tags?) - Remember a durable fact for later recall (e.g. a "
            "user preference, a project decision/conclusion, an important identifier).\n"
            "   - tool_memory_get(key) - Recall a previously remembered fact by key.\n"
            "   - tool_memory_search(tag) - Find all remembered facts with a given tag (e.g. 'project', "
            "'user_preference').\n"
            "   Remember SPARINGLY: ONLY store durable, cross-turn facts worth keeping. Do NOT store "
            "trivial or transient details, and do NOT duplicate what the conversation history already "
            "contains — history + compaction handles the process; memory is only for facts that must "
            "survive reliably across turns.\n"
            "   Memory tools are NOT for producing an answer: if the user asks for a piece of text "
            "(self-introduction, copywriting, explanation, chat), reply directly in plain text and "
            "NEVER call tool_memory_set just to 'store the answer'."
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

    if enabled_skills:
        from app.config import settings as _settings
        if getattr(_settings, "skills_lazy", False):
            # [工具懒加载 2026-10-09] 技能 schema 不再常驻；用 tool_search_tools 按需发现。
            tool_parts.append(
                "Skill tools (load_skill_<name>()): specialized skills exist but are NOT preloaded "
                "(to save context). FIRST call tool_search_tools(query=...) to find the matching "
                "skill(s), then call the returned load_skill_<name>() tool to load the full "
                "instructions BEFORE starting a task that matches one (documents, web/frontend, "
                "design, coding practices, teaching, research, etc.)."
            )
        else:
            # [token 优化 v10] 不再逐一列出全部技能名（30+ 技能约 1.3K 字符，固定随每次调用发出）。
            # [技能常驻] 技能清单 + 截断描述由 graph._build_tool_defs 作为**常驻** load_skill_*
            # schema 无条件挂载（17 技能约 1.7K token），系统提示词只保留这一行，避免重复描述。
            tool_parts.append(
                "Skill tools (load_skill_<name>()): specialized skills are available. Their inventory and "
                "truncated descriptions are already present in your tool schema — scan that list first, "
                "then call the matching load_skill_<name>() tool to load the full instructions BEFORE "
                "starting a task that matches one (documents, web/frontend, design, coding practices, "
                "teaching, research, etc.). Skills not in the schema were disabled by the user."
            )

    if tool_parts:
        parts.append("You can use the following tools:")
        parts.extend(tool_parts)

    parts.extend([
        "",
        "Instructions:",
        "- DECIDE FIRST what the user actually wants (most important, applies to EVERY request):\n"
        "    * Pure Q&A (asking, explaining, summarizing, translating, self-introduction, chat, opinions): "
        "answer directly in plain text. Do NOT create files, do NOT call tools, and do NOT output a plan/report. "
        "A request like \"introduce yourself\" or \"write a short text/a few sentences\" is plain Q&A, NOT a file task.\n"
        "    * File/project task: only when the user explicitly asks to create, modify, build, or save files, "
        "code, or projects → then use file tools and follow the planning/report conventions below.\n"
        "    * Never write files just to \"have done something\". If no tool is genuinely needed, just answer.",
        "- WORKFLOW (call tool → execute → summarize): call tools to gather info / perform actions; "
        "AFTER tool results come back, ALWAYS reply with a natural-language summary for the user. "
        "Do NOT keep calling the same tool, do NOT output an empty JSON like {}, and do NOT print "
        "tool-call JSON as text.",
        "- No knowledge base available; answer from your own knowledge; say so honestly if unsure.",
        "- Only call tools directly relevant to the request.",
        "- Docs: use docx/pdf/excel/pptx generator plugins, or tool_write_file for other formats.",
        "- Realtime/news/web info: tool_web_search(query, max_results) — built-in web search.",
        "- URL content: plugin_internet-search_tool_extract_urls.",
        "- HTTP: plugin_http-client_tool_http_get/_post/_request (headers as JSON string).",
        "- CRITICAL: tool_execute is for build/install/test/check/verify (npm install, npm run build, python -m py_compile, pytest, npm test, node --check). NEVER use it for curl/wget/ping/network — use http-client plugin.",
        "- KB export: kb-export tools.",
        *([
            "- Voice/TTS: use tool_tts_synthesize to turn reply/text into a downloadable .wav "
            "(saved to data/generated); tool_voice_transcribe to transcribe a local audio file to text. "
            "Speakers: Vivian/Serena/Uncle_fu/Dylan/Eric/Ryan/Aiden/Ono_anna/Sohee."
        ] if has_voice else []),
        *([
            "- Code execution (tool_code_exec — controlled executor): for multi-step exact computation, "
            "data cleaning/stats/transform, or string/regex handling, write a short Python program and run it "
            "in the controlled executor. Inside the sandbox os/subprocess/socket/open are unavailable; access "
            "files/network ONLY through the built-in api module: api.read_file / api.write_file / "
            "api.append_file / api.list_dir / api.glob / api.grep / api.web_search / api.save_artifact. "
            "On error read the traceback and rewrite (max 3 attempts). After success summarize in natural "
            "language; write large results to files with the normal file tools. Use tool_execute for "
            "shell/system commands — NOT the executor."
        ] if _codeact_enabled else []),
        "- If no tool fits, answer directly without calling tools.",
        "",
        "IMPORTANT - Before code/design tasks: FIRST load the relevant skill (if it is not already in your "
        "tool list, call tool_search_tools to find it, then call its load_skill_<name>()) for best practices, "
        "then write files with tool_write_file; use tool_execute for build/install/test/check (e.g. python -m py_compile) as needed.",
        "",
        "IMPORTANT - Documents (.docx/.pdf/.xlsx/.pptx): use the matching generator plugin "
        "(docx/pdf/excel/pptx-generator); section/slide schemas are in each tool's description. Files saved per your directory rules.",
        "",
        "IMPORTANT - Projects: write ALL code files first via tool_write_file (auto-creates dirs); do NOT use "
        "mkdir/Set-Content or 'npm create'/'create-react-app'/'create vite'; only then run tool_execute for install/build/test.",
        "",
        ARTIFACT_SKILL_HINT,
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
        "  To search files in a configured workspace, pass path=<absolute path> to tool_glob / tool_grep.",
        "  tool_grep / tool_glob return absolute paths when searching a custom path.",
        "",

        # ── 本地模型工具调用格式（对原生 tool_calls 模型无影响，仅引导弱模型）──
        "Tool-call format (for models without native function-calling support):",
        "  When you need to call a tool, output EXACTLY this single line, with NO other text:",
        '  <tool_call>{"name": "<tool_name>", "arguments": {"<param>": "<value>"}}</tool_call>',
        "  - <tool_name> must match a listed tool name exactly (e.g. tool_ls, tool_write_file).",
        "  - arguments is a JSON object with the tool's parameter names as keys.",
        "  - Use the <tool_call> marker and the keys \"name\" and \"arguments\" exactly as shown.",
        '  - Do NOT wrap tool calls in {"response": ...} or any other envelope.',
        "  - For your final answer, output plain text directly — do NOT wrap in JSON.",
    ])

    return "\n".join(parts)
