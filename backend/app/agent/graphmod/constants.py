"""拆分模块 `constants`（含 DOOM_LOOP_PROMPT、MAX_STEPS_PROMPT、_DEDUP_READONLY_TOOLS、_FINISH_REASON_MAP、_TASK_TOOL_SCHEMA、_TASK_TOOL_SUBAGENTS、_is_multi_agent_queue、_nearest_workspace_hint、_normalize_finish_reason、_permission_denied_msg）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──



import logging








from pathlib import Path























logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# P3: 循环护栏提示词（对齐 opencode MAX_STEPS_PROMPT 的收尾语义）

MAX_STEPS_PROMPT = (
    "CRITICAL - MAXIMUM STEPS REACHED\n\n"
    "本轮已达到单次请求允许的最大工具轮次，工具已禁用，请以纯文本回复。\n\n"
    "STRICT REQUIREMENTS:\n"
    "1. 不要再调用任何工具（包括读取、写入、编辑、搜索等）。\n"
    "2. 必须给出文字总结，包含：\n"
    "   - 已完成的步骤/文件\n"
    "   - 尚未完成的任务\n"
    "   - 建议的下一步操作\n"
    "3. 该约束优先于其他所有指令。\n\n"
    "报告纪律（重要）：\n"
    "- 停止的**唯一原因**是本轮工具轮次预算耗尽。如实这样写。\n"
    "- 严禁编造停止原因：不要臆测「权限不足 / Permission denied / 目录不存在 / "
    "模型不支持」等，除非你在前面的工具结果里**真的看到过**该错误文本。\n"
    "- 只陈述工具结果里出现过的信息；不确定的写成「未验证」。\n"
    "- 若你之前已成功写入文件（工具结果里有 Created/Overwritten/Deleted），"
    "必须列出来 —— 不要谎称未写入。"
)


# ── 零进展救援（reasoning 预算耗尽）────────────────────────────────────────
# 实测（deepseek-v4-flash，「在 D:\game 写 React 俄罗斯方块」）：单轮把 8192 输出
# token **全部**花在 reasoning_content 上，content 空、tool_calls 空、
# finish_reason=length → 循环退出，用户收到 2.7 万字内心独白而磁盘零文件。
# 这不是「截断」（截断仍有已交付内容），而是「什么都没做」，所以必须续跑。
ZERO_PROGRESS_ACK = (
    "[上一轮输出因达到 token 上限被截断，未产生任何正文或工具调用]"
)

ZERO_PROGRESS_PROMPT = (
    "CRITICAL - 上一轮你把全部输出预算耗尽在内部思考上，**没有产出任何正文，"
    "也没有调用任何工具**，任务实际上一件都没做成。\n\n"
    "现在立刻停止继续推演，按下面的顺序执行：\n"
    "1. **不要再做方案推演/自我讨论/列举计划** —— 那些已经浪费了一整轮预算。\n"
    "2. 如果任务需要落地文件/代码：**立刻调用写文件类工具"
    "（tool_write_file / tool_apply_patch / tool_append_file）开始写**，"
    "不要先解释再写，边写边推进。\n"
    "3. 如果任务只需回答：**直接给最终答案**，控制在几百字内。\n\n"
    "硬性约束：\n"
    "- 本轮必须至少产生一次工具调用或一段正文，否则任务再次失败。\n"
    "- 不要输出思考过程、不要复述计划、不要写「我将会…」这类将来时。\n"
    "- 需要信息就直接调工具去取，不要靠推理补全。\n"
)


# ── 超长内联正文救援（模型无视「长内容写文件」规则）──────────────────────────
# 实测（同一模型，同一「在 D 盘写个俄罗斯方块游戏」任务，2026-10-03）：
# 前几轮正常 ls/read_file，第 4 轮突然把 8192 输出预算全部花在**聊天窗口正文**上
# （content 24,258 字符的方案/分析报告，tool_calls=[]，finish_reason=length）。
# `_is_zero_progress` 因「content 非空」判 False → 零进展救援不触发 → while 条件
# 因「无 tool_calls」退出 → 磁盘零文件，用户收到一份被腰斩的 24K 字规划草稿。
#
# 与零进展的区别：不是「什么都没做」，而是「做了但做在聊天里而非磁盘上」。
# 因此话术不能说「没有产出任何正文」（那是假的、且会削弱模型信任），必须明确
# 告知「你被截断了」并把落盘动作前置。
OVERSIZED_PROSE_MIN_CHARS = 2000

OVERSIZED_PROSE_ACK = (
    "[上一轮输出因达到 token 上限被截断：正文超长且未调用任何工具]"
)

OVERSIZED_PROSE_PROMPT = (
    "CRITICAL - 上一轮你被**输出 token 上限截断**，而且你把整轮预算写成了聊天窗口里的"
    "长篇正文（未调用任何工具），所以任务实际上一件都没做成。\n\n"
    "硬性规则（不可协商）：\n"
    "- **禁止**在聊天窗口里写代码、HTML、文档正文或长篇方案；那会再次撞上上限并被截断。\n"
    "- 需要落盘的内容一律用写文件类工具分段写入："
    "tool_write_file（首段）→ tool_append_file（后续段），每段控制在几百行以内。\n"
    "- 如果文件很大，先只写骨架（结构 + 关键函数），再逐段补全，不要一次写完。\n"
    "- 需要信息就直接调工具去取，不要靠推理补全。\n\n"
    "如果你判断任务**不需要产出文件**（纯问答），则直接给出**几百字以内**的最终答案，"
    "不要展开成长文。\n\n"
    "本轮必须产生至少一次工具调用，或一段短答案；否则任务再次失败。"
)


# ── 工具参数截断守卫 ────────────────────────────────────────────────────────
# finish_reason=length 且仍带 tool_calls ⇒ 参数一定被输出上限砍断。`parse_tool_args`
# 会把半截 JSON「修复」成合法 dict，残缺字符串被当完整内容执行 → 静默写出损坏文件。
# 回给模型的文案必须给出可操作的下一步（分段写），否则模型会原样重试同样大的 payload。
_TRUNCATED_ARGS_ERROR = (
    "Error: '{tool}' 的调用参数因达到输出 token 上限（finish_reason=length）被截断，"
    "已阻止执行 —— 若强行执行会写出半截文件。\n"
    "请重试并显著缩小单次 payload：\n"
    "- 写文件：只写一个文件的**一部分**（如前 100 行），后续用 tool_append_file 续写；\n"
    "- 单个文件很大时，先只写骨架（结构 + 关键函数），再逐段补全；\n"
    "- 一次调用只写一个文件，不要把多个大文件塞进同一轮。"
)


# P3: Doom-loop 检测提示词（对齐 opencode processor.ts:DOOM_LOOP_THRESHOLD）

DOOM_LOOP_PROMPT = (
    "系统提示：检测到连续多轮调用完全相同的工具参数，疑似陷入死循环。"
    "请立即停止重复调用，改变策略（例如先读取/检查，再采取不同的操作），"
    "或基于已有信息直接给出最终回答。"
)

REPEAT_DELEGATION_PROMPT = (
    "系统提示：你刚刚用**完全相同的参数**再次委派了 tool_task，这是无效动作 —— "
    "子 Agent 的结论已经作为 tool 结果回到上下文里，原样重发不会得到新信息，"
    "只会白等一次子 Agent 运行。\n"
    "重要：sub-agent（explore / plan）**没有写文件的能力**，把「创建/实现/编写」这类"
    "任务委派给它们永远做不完。\n"
    "请现在就改为直接动手：需要落地代码/文件就用 tool_write_file / tool_apply_patch / "
    "tool_append_file，需要跑构建就用 tool_execute；只读调研才用 tool_task。"
    "如果你已经拿到了子 Agent 的结论，就基于它继续执行或直接作答。"
)


# P4: finish_reason 归一化映射（对齐 opencode FinishReason 六值，llm/src/schema/ids.ts:39）

_FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "max_tokens": "length",          # Anthropic/Bedrock 原生名
    "tool_calls": "tool-calls",
    "function_call": "tool-calls",   # 旧式 OpenAI
    "content_filter": "content-filter",
    "error": "error",
}

def _normalize_finish_reason(finish_reason: str | None) -> str:
    """把 LiteLLM/OpenAI 式 finish_reason 归一化为 opencode FinishReason 六值。

    None 视为正常结束（stop）：本地执行无 Provider 异常时的缺省语义。
    未知值归一化为 "unknown"（不视为已完成，保守续跑一轮）。
    """
    if not finish_reason:
        return "stop"
    return _FINISH_REASON_MAP.get(str(finish_reason).strip().lower(), "unknown")

def _permission_denied_msg(operation: str, path: str, tool_name: str = "") -> str:
    """生成可解释的权限拒绝消息，让 LLM 能据此调整路径/策略而非盲目重试。"""
    hint = _nearest_workspace_hint(path)
    tool = f" (tool={tool_name})" if tool_name else ""
    return (
        f"Permission denied: {operation} on '{path}'{tool}. {hint}\n"
        "这不是可重试的临时错误——请改用可写工作区内的路径，或告知用户将该路径添加到"
        "页面右上角「工作目录」中（添加后立即生效，无需重启）。"
    )

def _nearest_workspace_hint(path: str) -> str:
    """根据路径归属给出可解释的拒绝建议：受保护源码路径 / 就近可写工作区 / 完全外部。"""
    try:
        from app.permission import get_manager
        p = Path(path).resolve()
        for w in get_manager().list_workspaces():
            wp = Path(w).resolve()
            try:
                p.relative_to(wp)
                return "该路径位于工作区内但属于受保护的系统/源码路径（如 app、plugins、skills、.env），不可写入。"
            except ValueError:
                pass
            if wp != p and wp in p.parents:
                return f"该路径不在工作区内，但 '{wp}' 是可写工作区——请将文件写入 '{wp}' 之下。"
    except Exception:
        pass
    return "该路径不在当前可写工作区内（见系统提示词中的工作区列表）。"



# 读取类工具：只读文件/目录状态，结果可被后续写操作改变，因此缓存仅在"未发生写操作"时有效

_DEDUP_READONLY_TOOLS = {"tool_ls", "tool_read_file", "tool_glob", "tool_grep"}


# ── [opencode task tool] 主 Agent 可委派聚焦子任务的子 Agent 白名单 ──
# [build 合并] rag/code/web_search 已并入 build 自身（不再注册为独立总线 Agent），
# 仅保留 explore（只读探索）与 plan（规划）两个聚焦子 Agent 可委派。

_TASK_TOOL_SUBAGENTS = ("explore", "plan")

_TASK_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_task",
        "description": (
            "Launch a sub-agent (explore / plan) to handle a focused, independent subtask "
            "autonomously and return its final result. Use this tool when a piece of the request "
            "is independent and benefits from a dedicated context (e.g. read-only codebase "
            "exploration, or producing a structured implementation plan). You continue working "
            "while it runs, and may launch multiple sub-agents. Do NOT delegate work you can do directly yourself. "
            "A completed task returns a <task id=\"...\" state=\"completed\"> wrapper — pass that id back "
            "as task_id to continue (resume) the same sub-agent conversation instead of a fresh one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "A short (3-5 words) description of the task"},
                "prompt": {
                    "type": "string",
                    "description": "The detailed task for the sub-agent. It starts with fresh context (or resumes "
                    "a previous sub-agent conversation when task_id is given) — include all file paths and "
                    "background needed. Say clearly what to return.",
                },
                "subagent_type": {
                    "type": "string",
                    "enum": list(_TASK_TOOL_SUBAGENTS),
                    "description": "explore for read-only codebase exploration; plan for structured planning.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Only set to resume a previous sub-agent task: pass the id from a prior "
                    "tool_task result wrapper (<task id=\"...\">) to continue the same sub-agent conversation "
                    "instead of starting fresh.",
                },
                "background": {
                    "type": "boolean",
                    "description": "true launches the sub-agent asynchronously and returns immediately with a "
                    "<task id=\"...\" state=\"running\"> wrapper; you will be notified when it completes. "
                    "false (default) waits for the result before continuing.",
                },
            },
            "required": ["description", "prompt", "subagent_type"],
        },
    },
}

def _is_multi_agent_queue(q) -> bool:
    """判断事件队列是否来自 multi-agent 流（子 Agent 面板事件只有 multi-agent UI 消费）。"""
    try:
        from app.agent.stream_events import AgentEventCollector, TaggedEventQueue
        return isinstance(q, (AgentEventCollector, TaggedEventQueue))
    except Exception:  # noqa: BLE001
        return False



__all__ = ["DOOM_LOOP_PROMPT", "MAX_STEPS_PROMPT", "OVERSIZED_PROSE_ACK", "OVERSIZED_PROSE_MIN_CHARS", "OVERSIZED_PROSE_PROMPT", "REPEAT_DELEGATION_PROMPT", "ZERO_PROGRESS_ACK", "ZERO_PROGRESS_PROMPT", "_TRUNCATED_ARGS_ERROR", "_DEDUP_READONLY_TOOLS", "_FINISH_REASON_MAP", "_TASK_TOOL_SCHEMA", "_TASK_TOOL_SUBAGENTS", "_is_multi_agent_queue", "_nearest_workspace_hint", "_normalize_finish_reason", "_permission_denied_msg"]
