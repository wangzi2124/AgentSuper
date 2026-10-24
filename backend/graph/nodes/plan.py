"""任务分解节点（对齐 agent.md §3.2 / §7.5.1）。

无模型时（M1 快速路径）：把用户请求转成单步计划。
注入 `plan_model` 时（M3）：用 LLM 分解为结构化子任务（含 agent 归属），
供并行扇出与委派使用。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from models.router import TierModel, resolve_model
from prompts.loader import PromptLoader

from ..tasks import KNOWN_AGENTS, is_explicit_empty_plan, parse_task_specs
from ..usage import usage_from_result
from .base import content_text

logger = logging.getLogger(__name__)

MAX_TASKS = 5

# ── 计划档位（agent-1.md §2.2 / §2.4）──
# 三档：single（单任务快速路径，不调模型）/ serial（串行分解，调 medium 模型）
# / parallel（并行扇出，调模型 + Send，Step 3 门禁后才开）。
# 由用户措辞的任务特征决定，不做二元开关 —— 大多数请求是 single/serial。
_SERIAL_SIGNALS = ("然后", "接着", "之后", "再", "基于", "根据以上", "根据上面", "依次", "按顺序")
_PARALLEL_SIGNALS = ("分别", "同时", "各自", "并行", "三个", "多个", "逐一", "各自独立")
# 单一明确动词快速路径：短描述（<50 字）或无分解诉求时，恒单任务。
_SINGLE_MAX_LEN = 50
# 长描述（>100 字且无问号）视为复杂 → 可走串行/并行分解。
_LONG_DESC_LEN = 100


def classify_plan_tier(text: str) -> str:
    """按用户措辞判定计划档位（§2.2 / §2.4）。

    - "single"  ：短描述 / 无分解信号 → 单任务快速路径（不调模型）
    - "serial"  ：含串行信号（然后/接着/基于以上…）→ 串行分解
    - "parallel"：含并行信号（分别/同时/各自…）或长描述性任务 → 可并行扇出

    无信号时按长度：<50 字 → single；≥100 字且无问号 → parallel-capable；否则 serial。
    """
    s = (text or "").strip()
    if not s:
        return "single"
    has_parallel = any(sig in s for sig in _PARALLEL_SIGNALS)
    has_serial = any(sig in s for sig in _SERIAL_SIGNALS)
    # 短问句是单次问答，不是并行任务：「项目代号和端口分别是什么？」里的
    # 「分别」只是并列疑问，不该触发 plan LLM 分解（plan.md 会把「简单问答」
    # 判成空计划 → 本轮没有子任务 → synthesize 只能拿草稿/旧结果）。
    if len(s) < _SINGLE_MAX_LEN and ("?" in s or "？" in s):
        return "serial" if has_serial else "single"
    # 并行信号优先（用户明确要"分别/同时"）；但串行信号与之并存时以串行为准
    # （"先 A 然后分别 B/C" = 有依赖，不能并行）。
    if has_parallel and not has_serial:
        return "parallel"
    if has_serial:
        return "serial"
    # 无显式分解信号：短请求走单任务；长描述性任务（无问号）视为复杂。
    if len(s) < _SINGLE_MAX_LEN:
        return "single"
    if len(s) > _LONG_DESC_LEN and "?" not in s and "？" not in s:
        return "parallel"
    return "serial"


# 纯寒暄/致谢/应答识别（全串锚定，正文带任何任务内容即不命中）——
# 这类消息若进工具循环，弱本地模型会臆造文件名并把「文件不存在」错误
# 当成最终答案（实测 qwen2.5:3b 对「你好」返回「错误：文件不存在 工作区.txt」）。
# 单元可重复（对齐 plan.md「及其重复、变体」）：「你好你好」「喂，喂」「ok ok」命中；
# 「在吗？帮我查下这个报错」等带任务正文的仍不命中（正文不在单元/标点字符类内）。
_GREETING_UNIT = (
    r"(?:你好+|您好|嗨+|哈喽|哈啰?|hi+|hello|hey|喂+|"
    r"在吗|在么|在不在|有人吗|有人么|请问在吗|"
    r"早上好|早安|上午好|中午好|下午好|傍晚好|晚上好|晚安)"
)
_GREETING_RE = re.compile(
    rf"^(?:{_GREETING_UNIT}[!！~～。.，,\s?？]*)+$",
    re.IGNORECASE,
)
_THANKS_UNIT = r"(?:谢谢+|谢谢你|感谢你?|多谢+|谢啦|thanks?|thx|thank u)"
_THANKS_RE = re.compile(
    rf"^(?:{_THANKS_UNIT}[!！~～。.，,\s?？]*)+$",
    re.IGNORECASE,
)
_ACK_UNIT = r"(?:好的+|okay|ok|嗯+|哦+|噢+|收到了?|明白|了解|知道了?)"
_ACK_RE = re.compile(
    rf"^(?:{_ACK_UNIT}[!！~～。.，,\s?？]*)+$",
    re.IGNORECASE,
)
# 测试性输入（plan.md 判定标准：非实质请求）——回复 greeting 自我介绍，
# 让用户看到助手存活；「测试一下/测个功能」等带正文的不命中。
_TEST_RE = re.compile(
    r"^(?:test|testing|测试)[!！~～。.，,\s?？]*$",
    re.IGNORECASE,
)

_INTRO_RE = re.compile(
    r"^(?:"
    r"(?:请|麻烦)?(?:简单|简要|简短)?(?:地)?(?:介绍|说说|讲讲)(?:一下)?(?:你自己|你)?"
    r"|你是谁|你是什么|你是做什么的"
    r"|你能做什么|你能干什么|你会什么|你可以做什么"
    r"|你有哪些功能|你有什么功能|你有什么能力"
    r"|怎么用|如何使用|使用说明|自我介绍"
    r")[!！~～。.，,\s?？]*$",
    re.IGNORECASE,
)

SELF_INTRO_MAIN = (
    "你好！我是基于 RAG + Agent（检索增强生成）的知识库智能系统，"
    "支持文档上传、向量检索、多模型对话、Skills 和 Plugins 动态扩展。\n\n"
    "我可以帮你：\n"
    "- 文档上传：上传并解析 PDF、Word、Excel、Markdown 等文档\n"
    "- 向量检索：基于知识库做语义检索与引用作答\n"
    "- 多模型对话：按任务切换不同模型进行对话与推理\n"
    "- Skills：加载专项技能与工作流\n"
    "- Plugins：动态接入工具与外部能力\n"
    "- 工作区文件：读取、写入、编辑与检索\n\n"
    "直接告诉我目标即可，例如「上传这份文档并总结」「在知识库中检索 xxx」「用某个模型分析这份资料」。"
)

SMALLTALK_REPLIES = {
    "greeting": SELF_INTRO_MAIN,
    "intro": SELF_INTRO_MAIN,
    "thanks": "不客气！还有其它需要随时告诉我。",
    "ack": "好的，需要时随时找我。",
}


def classify_smalltalk(text: str) -> str | None:
    """纯寒暄返回 'greeting'/'thanks'/'ack'；含任务内容或为空返回 None。"""
    s = (text or "").strip()
    if _GREETING_RE.match(s):
        return "greeting"
    if _THANKS_RE.match(s):
        return "thanks"
    if _ACK_RE.match(s):
        return "ack"
    if _TEST_RE.match(s):
        return "greeting"
    if _INTRO_RE.match(s):
        return "intro"
    return None


def _latest_human_has_blocks(state: dict[str, Any]) -> bool:
    """最近一条 human 消息是否为分块 content（带图片等附件时不做寒暄短路）。"""
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return isinstance(getattr(msg, "content", ""), list)
    return False


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        role = getattr(msg, "type", None)
        if role in ("human", "user"):
            return content_text(getattr(msg, "content", ""))
    messages = state.get("messages") or []
    if messages:
        return content_text(getattr(messages[-1], "content", ""))
    return ""


def _single(text: str) -> list[dict[str, Any]]:
    return [{"id": "t1", "desc": text or "完成用户请求", "status": "pending"}]


def make_plan_node(
    model: Any | None = None,
    max_tasks: int = MAX_TASKS,
) -> Callable[[dict[str, Any]], Any]:
    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("task_plan"):
            return {}

        request = _last_user_text(state)

        if not _latest_human_has_blocks(state):
            kind = classify_smalltalk(request)
            if kind:
                # 短路：任务直接标记完成，草稿即回复 —— 不进工具循环、不调 LLM。
                # 路由：route_after_plan（无待办）→ execute 空转 → supervisor →
                # synthesize（sub_results 为空 → 采用 draft_output）→ END。
                return {
                    "task_plan": [{"id": "t1", "desc": request, "status": "done"}],
                    "draft_output": SMALLTALK_REPLIES[kind],
                }

        if model is None:
            return {"task_plan": _single(request), "plan_tier": "single"}

        # 三档策略（agent-1.md §2.2）：单任务快速路径**不调模型**。
        # 只有串行分解/并行扇出才需要 medium 模型生成本结构化计划。
        tier = classify_plan_tier(request)
        if tier == "single":
            return {"task_plan": _single(request), "plan_tier": "single"}

        usage: dict[str, Any] = {}
        raw_content = ""
        try:
            prompt = PromptLoader().load("nodes/plan.md", content=request)
            res = await model.ainvoke([{"role": "user", "content": prompt}])
            usage = usage_from_result(res, getattr(model, "tier", None))
            raw_content = getattr(res, "content", "") or ""
            specs = parse_task_specs(raw_content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan decomposition failed, using single task: %s", exc)
            specs = []

        if not specs:
            if raw_content and is_explicit_empty_plan(raw_content):
                # 显式空计划（plan.md 用于寒暄/简单问答）不代表任务已完成：
                # 真正寒暄已在 filter / classify_smalltalk 短路，走到这里的是
                # 需要回答的问题。回退单任务交给主 Agent（带历史 + 工具）作答，
                # 否则会把「你好！我是工作区助手…」当成本轮答案。
                logger.info(
                    "plan LLM returned empty plan; fall back to single task: %r",
                    request[:80],
                )
            return {"task_plan": _single(request), "plan_tier": "single", "token_usage": usage}

        tasks = [
            {
                "id": s.id,
                "desc": s.objective,
                "agent": s.agent,
                "context": s.context,
                "deliverable": s.deliverable,
                "status": "pending",
            }
            for s in specs[:max_tasks]
            if s.agent in KNOWN_AGENTS
        ]
        if not tasks:
            return {"task_plan": _single(request), "plan_tier": "single", "token_usage": usage}
        # 计划生成成功：携带档位（parallel 需 Step 3 门禁后才会真正扇出）。
        return {"task_plan": tasks, "plan_tier": tier, "token_usage": usage}

    return plan_node
