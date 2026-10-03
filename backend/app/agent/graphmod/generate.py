"""拆分模块 `generate`（含 RAGAgentGenerate）。

原文件 docstring: (无)"""
# ── 复制自原模块的顶层 import ──
import asyncio


import logging





import time as tmod





from app.context.token_counter import truncate_messages as _truncate_messages

from app.context.token_counter import sanitize_tool_messages

from app.context.tool_output import bound_tool_output, prune_tool_outputs

from app.context.tool_dedup import ToolResultDedup

from app.context.budget import usable_context_tokens, compaction_threshold_tokens, prune_protect_tokens, prune_minimum_tokens
from app.context.budget import llm_call_budget
from app.context.token_counter import estimate_tokens_messages
from app.agent.image_processor import describe_image

from app.utils.json_repair import parse_tool_args


from langchain_core.messages import BaseMessage, HumanMessage, AIMessage







from app.config import settings



from app.monitor import record_model_call

from app.trace_log import trace, trace_messages  # [token trace v7]


from .tools import RAGAgentTools
# ── 跨子模块依赖（自动生成）──
from .base import is_weak_model
from .constants import DOOM_LOOP_PROMPT, REPEAT_DELEGATION_PROMPT
from .constants import MAX_STEPS_PROMPT
from .constants import OVERSIZED_PROSE_ACK, OVERSIZED_PROSE_MIN_CHARS, OVERSIZED_PROSE_PROMPT
from .constants import _TRUNCATED_ARGS_ERROR
from .constants import ZERO_PROGRESS_ACK, ZERO_PROGRESS_PROMPT
from .constants import _DEDUP_READONLY_TOOLS
from .constants import _normalize_finish_reason
from .task_registry import get_task_registry as _get_task_registry
from .state import AgentState
from .state import _ZERO_USAGE
from .state import _attachment_parts

# [弱模型鲁棒性] 精简系统提示：本地/小参数模型不挂任何工具（见 _build_tool_defs），
# 纯文本问答，避免乱调工具导致空输出（{}）或死循环。
_WEAK_SYSTEM_PROMPT = (
    "你是 AgentSuper 的 AI 助手。请用简洁的中文直接回答用户问题。\n"
    "你无法调用文件系统/工具，只能进行纯文本问答。若需要用户手动操作（如读写文件、"
    "执行命令），请明确告诉用户怎么做。\n"
    "回答要求："
    "1. 直接给出答案，不要输出 JSON、工具调用标记或任何代码外壳；\n"
    "2. 不要编造文件内容或系统状态，不确定就如实说明。"
)

# [弱模型] 关闭强模型兜底后，弱模型未能返回有效回答时的直接答复：提示用户手动切换更强模型。
_WEAK_MODEL_SWITCH_HINT = (
    "当前模型（弱模型）不支持工具能力，且本次未能返回有效回答，已按配置停用默认强模型兜底。\n"
    "请在上方模型选择器中切换到更强的模型（如 deepseek-v4 等）后重新提问。"
)


def _is_valid_answer(text: str | None) -> bool:
    """最终回答是否可用：非空，且不是自造 JSON / 工具调用标记等垃圾输出。"""
    from app.utils.json_repair import is_unparsed_json_answer, is_tool_call_markup
    t = (text or "").strip()
    if not t:
        return False
    return not is_unparsed_json_answer(t) and not is_tool_call_markup(t)


logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class RAGAgentGenerate(RAGAgentTools):
    # 产出文件的工具 → 从工具实参提取路径的字段（[C5] 步骤文件交接用）
    _FILE_TOOL_ARGS = {
        "tool_write_file": ("path",),
        "tool_append_file": ("path",),
        "tool_edit_file": ("path",),
        "tool_delete_file": ("path",),
        "tool_rename_file": ("path", "new_path"),
        "plugin_docx-generator_tool_create_docx": ("output_path",),
        "plugin_pdf-generator_tool_create_pdf": ("output_path",),
        "plugin_excel-generator_tool_create_excel": ("output_path",),
        "plugin_kb-export_tool_export_kb_to_docx": ("output_path",),
    }

    @staticmethod
    def _extract_step_files(tool_calls) -> list[str]:
        """从一轮工具调用实参中提取产出文件路径（供 STEP_STATE 交接）。"""
        files: list[str] = []
        for tc in tool_calls or []:
            args = parse_tool_args(getattr(tc.function, "arguments", ""))
            if not isinstance(args, dict):
                continue
            for key in RAGAgentGenerate._FILE_TOOL_ARGS.get(tc.function.name, ()):
                v = args.get(key)
                if isinstance(v, str) and v.strip():
                    files.append(v.strip())
        return list(dict.fromkeys(files))

    async def _step_summarize(self, messages: list[dict], budget: int) -> list[dict]:
        """[C5 · 方案 D] 小步快走摘要替换：用 HierarchicalSummarizationMiddleware
        把旧轮次压成摘要，上下文只装 [摘要 checkpoint + 最近一轮 + 当前步]。

        与既有 compaction（阈值触发、anchored Task checkpoint）互补：本方法是
        **结构性**的——长任务周期性执行，无论上下文是否触顶都把旧轮次换成摘要，
        从根源上避免上下文随轮次线性膨胀（而非等它涨到阈值再清理）。

        保留最近 keep 条消息（覆盖刚完成一轮的 assistant+tool 结果），
        旧轮次进入摘要；摘要失败回退原列表（不阻断执行）。
        """
        try:
            from app.middleware.summarization import HierarchicalSummarizationMiddleware
            summarizer = HierarchicalSummarizationMiddleware(
                model=settings.summarization_model or self.model,
                api_key=settings.summarization_api_key or settings.llm_api_key,
                api_base=settings.summarization_api_base or settings.llm_api_base,
                trigger=("tokens", 1),  # 始终压缩（结构性小步，而非阈值触发）
                keep=("messages", max(2, settings.step_summary_keep_messages)),
            )
            compacted = await summarizer.apply(messages)
            compacted = sanitize_tool_messages(compacted)
            if estimate_tokens_messages(compacted) > budget:
                compacted = sanitize_tool_messages(
                    _truncate_messages(compacted, max_tokens=budget, reserve_tokens=0)
                )
            return compacted
        except Exception as e:  # noqa: BLE001 —— 摘要失败不阻断执行
            logger.warning("step summarize failed, keep raw context: %s", e)
            return messages

    async def _generate(self, state: AgentState) -> dict:
        """[两段式] 生成回答，并在弱模型收尾失败时用强模型**完整重跑**一次。

        - 弱模型跑过工具轮 → `_generate_impl` 内先做两段式收尾（强模型基于工具记录总结）；
        - 若最终回答仍无效（弱模型压根没调工具、吐 {} / 工具标记 / 自造 JSON）→ 用强模型
          完整重跑 `_generate_impl`（含工具循环），而不是拿 `tools=None` 的残缺上下文去问
          强模型（那会诱发强模型把工具调用当文本输出）。
        """
        out = await self._generate_impl(state)
        if (
            not _is_valid_answer(out.get("answer"))
            and is_weak_model(out.get("model") or "")
            and settings.empty_answer_retry
            and settings.empty_answer_fallback_model
            and settings.weak_model_strong_fallback
            and not state.get("_weak_rerun")
        ):
            strong = self._resolve_fallback_model(out.get("model") or "")
            if strong and strong != out.get("model"):
                logger.info("weak answer invalid → full rerun via model=%s", strong)
                state["_weak_rerun"] = True
                state["model"] = strong
                out = await self._generate_impl(state)
        if not _is_valid_answer(out.get("answer")):
            if is_weak_model(out.get("model") or "") and not settings.weak_model_strong_fallback:
                out["answer"] = _WEAK_MODEL_SWITCH_HINT
            else:
                out["answer"] = "（模型未返回有效内容，请重试或更换模型。）"
            out["messages"] = [AIMessage(content=out["answer"])]
        return out

    async def _generate_impl(self, state: AgentState) -> dict:
        """调用LLM生成回答，支持多轮工具调用。"""
        _gen_start = tmod.time()
        self._usage_accum = dict(_ZERO_USAGE)
        self._cost_accum = 0.0
        dedup = ToolResultDedup()
        # [B5] 本次请求的模型：预算（截断/压缩/prune）按其声明的 context_length 收窄
        _model_hint = state.get("model") or self.model
        from app.context.compaction import ContextCompactor
        compactor = ContextCompactor(
            model=settings.summarization_model or self.model,
            api_key=settings.summarization_api_key or settings.llm_api_key,
            api_base=settings.summarization_api_base or settings.llm_api_base,
            threshold=compaction_threshold_tokens(_model_hint),
            tail_turns=settings.context_tail_turns,
            preserve_recent_tokens=settings.context_preserve_recent_tokens,
        )
        self._push_event(state, {"type": "step_start", "step_id": "generate", "name": "生成回答", "status": "running"})

        context_text = ""
        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)
        # [token 优化 v2] system 保持完全稳定 → 最大化 DeepSeek 前缀缓存命中（命中按 0.1x 计费）
        # RAG 检索结果改放 user 消息前缀（见下方 user 消息构建），避免 system 每次变化导致缓存整体失效。
        # [弱模型鲁棒性] 弱模型用精简系统提示（更短、少工具说明，降低空输出/乱调工具）
        _weak = is_weak_model(_model_hint)
        full_system_prompt = (
            _WEAK_SYSTEM_PROMPT if (settings.weak_model_simple_prompt and _weak) else self.system_prompt
        )
        # [会话目录] 本会话绑定的工作目录追加为 system 末尾（同一目录内保持稳定）
        if state.get("_cwd"):
            full_system_prompt += (
                "\n\n[会话工作目录]\n"
                f"Current session working directory: {state['_cwd']}\n"
                "Relative paths in file tools (tool_ls/read_file/write_file/append_file/edit_file/"
                "glob/grep/execute) resolve under this directory. Files written here are allowed without permission."
            )

        # [token 优化 v5] 按需挂载：首轮按问题关键词筛选工具 schema
        # [token 优化 v15] 传入 conversation_id → 会话级缓存：同会话内只增不减，
        # system+tools 前缀跨请求字节稳定 → DeepSeek 前缀缓存命中
        # [弱模型] 弱模型不挂任何工具（_build_tool_defs 直接返回 []，纯文本问答）
        tool_defs = self._build_tool_defs(
            state.get("question", ""),
            conversation_id=state.get("conversation_id", ""),
            model=_model_hint,
        )

        messages = [
            {"role": "system", "content": full_system_prompt},
        ]
        if state.get("history"):
            messages.extend(state["history"])

        # Build user content: text only or multimodal if files attached
        # [token 优化 v2] RAG 上下文改放 user 消息前缀（system 保持稳定 → 命中前缀缓存）
        user_question = (
            f"Retrieved Context:\n{context_text}\n\n---\n\n{state['question']}"
            if context_text else state["question"]
        )
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": user_question}]
            # [F8 后端] 附件处理：
            #   - image/* → 规格化后的多模态 image_url 数据块（LLM 原生看图）
            #   - 其余（pdf/docx/xlsx/txt/md/csv/json 等）→ 经 LangChain document
            #     loaders 解析为文本上下文（attachment_loader），让模型能读到文档正文。
            image_files, text_ctx = _attachment_parts(user_files)
            # [能力驱动视觉] 按「模型管理」声明的 vision 能力决定是否附图：
            #   vision=True（含未收录模型，默认附图不臆断）→ 原生 image_url 数据块；
            #   vision=False（明确无视觉）→ 不附图、仅提示文件名，由 caption 桥（describe_image）
            #     注入文本描述 / OCR，模型依然能"读懂"图里内容。
            from app.models.catalog import read_capabilities as _read_caps
            _model_vision = _read_caps(_model_hint).get("vision")
            if _model_vision is not False:
                for img in image_files:
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img['mime_type']};base64,{img['data']}"},
                    })
            elif image_files:
                _names = "、".join(im.get("filename", "") or f"图片{i+1}" for i, im in enumerate(image_files))
                user_content.append({"type": "text", "text": f"[附加图片：{_names}]（当前模型不支持视觉，未附图，以下描述供参考）"})
            # [F8] 图片描述桥：视觉 LLM caption 注入文本（非视觉主模型/子 Agent 也能看图）
            if image_files and settings.image_vlm_caption and settings.image_caption_model:
                caps = []
                for img in image_files:
                    cap = await describe_image(
                        img.get("data") or "", img.get("mime_type") or "",
                        img.get("filename") or "",
                    )
                    if cap:
                        caps.append(f"[图片 {img.get('filename', '')}]: {cap}")
                if caps:
                    user_content.append({"type": "text", "text": "[图片描述]\n" + "\n".join(caps)})
            if text_ctx:
                user_content.append({"type": "text", "text": text_ctx})
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_question})

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        # [token 优化 v4] 压缩优先于硬截断：首轮若已超压缩阈值，先 LLM 压缩（保留事实摘要），
        # 避免直接丢弃旧历史导致模型失忆重做（重做比压缩更贵）。截断仅作最后兜底。
        # 工具循环内每轮已有同款 压缩→截断 闭环，此处在入口补齐，覆盖多轮对话 history 场景。
        if compactor.should_compact(messages):
            self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
            old_count = len(messages)
            messages = await compactor.compact(messages)
            messages = sanitize_tool_messages(messages)
            if state.get("_task"):
                state["_task"].record_compaction()
            self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})
        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(_model_hint), reserve_tokens=0, tool_defs=tool_defs))
        trace_messages("graph.entry_ready", messages, tool_defs=tool_defs)  # [token trace v7]

        response = await self._llm_call(model, messages, tool_defs, state=state)
        msg = response.choices[0].message
        # P4: 读取并归一化 finish_reason（对齐 opencode FinishReason：tool-calls/unknown 不算完成）
        finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))

        # [F8 · D 步 每轮不重发] 首轮已展示图片 → 后续工具轮替换为占位文本，
        # 避免每轮重发大 base64 撑爆上下文（图片描述/OCR 文本仍保留供模型参照）。
        if getattr(msg, "tool_calls", None):
            for m in messages:
                if isinstance(m.get("content"), list):
                    m["content"] = [
                        ({"type": "text", "text": "[已附图：图片已在首轮展示]"}
                         if isinstance(p, dict) and p.get("type") == "image_url" else p)
                        for p in m["content"]
                    ]

        # 硬兜底：单次请求内最多 LLM 调用轮数（每轮 = 一次完整 LLM 调用）
        max_tool_rounds = settings.max_tool_rounds
        # 主步骤上限（对齐 opencode agent.steps）：生效上限 = min(MAX_STEPS, MAX_TOOL_ROUNDS)。
        # [C3] 该上限必须真正作为循环边界使用。旧实现只拿它判断何时注入 MAX_STEPS_PROMPT，
        # 循环上界仍是 rounds < max_tool_rounds —— 默认 max_steps=24 > max_tool_rounds=8 时
        # MAX_STEPS 完全是 no-op（提示在第 8 轮注入，24 永远到不了），配置项形同虚设。
        effective_max_steps = min(max_tool_rounds, max(1, settings.max_steps))
        # Doom-loop：连续相同指纹 N 轮注入提示；升级到 doom_loop_max_strikes 次后强制收尾
        doom_threshold = max(2, settings.doom_loop_threshold)
        doom_max_strikes = max(1, settings.doom_loop_max_strikes)
        doom_fingerprints: list[str] = []
        doom_strikes = 0
        # 重复委派守卫：本轮已发起过的 tool_task 指纹（子 Agent 结论已回灌上下文，
        # 原样重发必然白等一次完整子 Agent 运行 —— 实测 qwen3.5:4b 连续两轮把
        # 「创建 React 游戏」原样委派给 plan，耗时 2.5 分钟且一个文件都没写）
        delegated_tasks: set[str] = set()
        repeat_delegation_strikes = 0
        steps_prompt_injected = False
        rounds = 0
        tool_calls_count = 0
        # [零进展救援] 思考模型可能把整轮输出预算烧在 reasoning 上（实测
        # deepseek-v4-flash ct=8192/reasoning=8192），content 空 + 无 tool_calls +
        # finish=length → 什么都不产出。此处不续跑任务必败，故在**主循环内部**追加
        # 有限次「强制产出」重试：复用现有工具执行/doom-loop/预算机制，模型一旦
        # 改口调工具即回到正常流程（而非像循环外补丁那样只能收尾）。
        rescue_budget = max(0, int(settings.zero_progress_rescue_attempts))
        rescue_pending = False
        rescue_used = 0
        # 触发本次救援的档位（None=无）；循环内据它决定能否走「已拿到正文就收尾」捷径。
        # 注意 `msg` 在该判断点仍是**触发救援那一轮**的响应，语义必须显式携带。
        rescue_kind: str | None = None

        def _rescue_kind(m, fr) -> str | None:
            """本轮属于哪一档「没推进任务」，None 表示正常。

            两档互补，顺序即优先级（先判零进展，避免话术说「没有产出任何正文」——
            超长正文档正文非空，那句话是假的）：
              a) zero_progress   —— 思考烧光预算，正文与工具皆空；
              b) oversized_prose —— 无视「长内容写文件」规则，把预算全写成聊天正文，
                                   被截断且零工具调用（`_is_zero_progress` 判 False）。

            **零进展档的 finish_reason 门禁是分情况的**：推理回退（`_content_from_reasoning`）
            无条件救援 —— 那一档的 content 是思考独白，按定义不是交付给用户的回答，而实测
            思考模型烧光预算后 Provider 会归一化成 `stop` 而非 `length`（content 空 +
            reasoning 10,219 字符 + finish=stop），若用 length 门禁就会漏掉、只留下
            291 字诊断文案。真正空白的 content 则仍要求 `length`：不带 `_content_from_reasoning`
            的空回答归既有的 EMPTY_ANSWER 重试 / 弱模型强模型兜底路径管（`test_graphmod_generate_core.py`
            锁定了那套轮次），这里抢过来会多烧一轮并挤掉强模型兜底。
            超长正文档同样必须靠 `length` 证明「非空正文其实是被砍断的残稿」，
            否则会对正常的短问答多问一轮。
            """
            if getattr(m, "tool_calls", None):
                return None
            if self._is_zero_progress(m):
                if str(fr) == "length" or getattr(m, "_content_from_reasoning", False):
                    return "zero_progress"
                return None
            if str(fr) == "length" and self._is_oversized_prose(m, fr):
                return "oversized_prose"
            return None

        def _maybe_rescue(m, fr, round_no) -> bool:
            """命中救援档就注入催促并置位 `rescue_pending`，让主循环**再问一轮**。

            刻意留在主循环内（而不是循环外收尾补丁）：下一轮模型若改口调工具，会照常
            走完整执行路径，任务得以真正完成；写成循环外收尾就只能拿到一段文字，
            且工具已被禁用。判定必须在**首轮响应之后、循环之前**也执行一次 ——
            `while` 条件只看 `tool_calls / tool-calls / rescue_pending`，若首轮就是
            超长内联正文，循环根本不会进入，救援形同虚设（实测第 4 轮才触发时正常，
            首轮就触发时静默失效）。
            """
            nonlocal rescue_budget, rescue_used, rescue_pending, rescue_kind
            kind = _rescue_kind(m, fr)
            if kind is None or rescue_budget <= 0 or steps_prompt_injected:
                return False
            rescue_budget -= 1
            rescue_used += 1
            rescue_pending = True
            rescue_kind = kind
            logger.warning(
                "Zero-progress round %d (%s; finish=length, content_chars=%d); "
                "injecting forced-output nudge (rescue %d/%d)",
                round_no,
                "oversized inline prose, zero tool calls" if kind == "oversized_prose"
                else "reasoning consumed output budget",
                len(m.content or ""),
                rescue_used, max(0, int(settings.zero_progress_rescue_attempts)),
            )
            self._chainlog_zero_progress(
                state=state, model=model, finish_reason=fr,
                rounds=round_no,
                input_tokens=int((self._usage_accum or {}).get("input", 0)),
                output_tokens=int((self._usage_accum or {}).get("output", 0)),
                reasoning_tokens=int((self._usage_accum or {}).get("reasoning", 0)),
                cost=round(float(getattr(self, "_cost_accum", 0.0)), 6),
                answer_chars=len(m.content or ""),
                from_reasoning=bool(getattr(m, "_content_from_reasoning", False)),
                event="generate.zero_progress_rescue",
            )
            self._push_event(state, {
                "type": "step_end", "step_id": "zero_progress",
                "name": "零进展救援", "status": "running",
                "detail": (
                    "上一轮把输出预算写成超长聊天正文、未调用任何工具且被截断，"
                    "注入强制落盘提示重试"
                    if kind == "oversized_prose" else
                    "上一轮思考耗尽输出预算、未产出任何内容，注入强制产出提示重试"
                ),
            })
            # 只注入短确认句 + 针对性指令，**不回灌**上一轮那 2 万多字正文/独白（纯浪费，
            # 而且正是它吃掉了输出预算）。
            if kind == "oversized_prose":
                messages.append({"role": "assistant", "content": OVERSIZED_PROSE_ACK})
                messages.append({"role": "user", "content": OVERSIZED_PROSE_PROMPT})
            else:
                messages.append({"role": "assistant", "content": ZERO_PROGRESS_ACK})
                messages.append({"role": "user", "content": ZERO_PROGRESS_PROMPT})
            return True

        # 首轮响应同样纳入救援判定（见 `_maybe_rescue` docstring 的说明）。
        _maybe_rescue(msg, finish_reason, 0)
        # [token 优化 v5] 已使用工具集合：每轮重挂载时保留，避免模型想复用却被移除
        used_tools: set[str] = set()
        while (
            msg.tool_calls or finish_reason == "tool-calls" or rescue_pending
        ) and rounds < effective_max_steps:
            rounds += 1
            # 本轮是否由「零进展救援」触发；进入即清标志（下一轮若再次零产出，
            # 由循环末尾的检测重新置位），避免救援轮把 while 条件永久撑开。
            is_rescue_round = rescue_pending
            rescue_pending = False
            # 统一出口：本轮的工具调用列表。litellm 在无工具时给 `tool_calls=None`，
            # 救援轮必然走这条路径 —— 后续所有遍历一律用它，避免 `for tc in None` 崩。
            # 统一出口：本轮的工具调用列表。litellm 在无工具时给 `tool_calls=None`，
            # 救援轮必然走这条路径 —— 后续所有遍历一律用它，避免 `for tc in None` 崩。
            cur_tool_calls = list(getattr(msg, "tool_calls", None) or [])

            # [opencode background] 每轮吸收本会话已完成的后台任务结果（合成 assistant 消息）
            bg_results = _get_task_registry().drain_background_results(state.get("conversation_id", ""))
            if bg_results:
                messages.append({
                    "role": "assistant",
                    "content": "后台任务已完成，请把结果纳入你的回答（无需再委派）：\n" + "\n\n".join(bg_results),
                })

            # Compaction: compress old messages when context grows large
            # （先回溯清理旧工具输出，再判断是否触发压缩）
            trace_messages("graph.round_start", messages)  # [token trace v7]
            messages = prune_tool_outputs(
                messages,
                protect_tokens=prune_protect_tokens(_model_hint),
                minimum_tokens=prune_minimum_tokens(_model_hint),
                tail_turns=settings.context_tail_turns,
            )
            trace_messages("graph.pre_compact", messages, threshold=compaction_threshold_tokens(_model_hint))  # [token trace v7]
            if compactor.should_compact(messages):
                self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
                old_count = len(messages)
                messages = await compactor.compact(messages)
                messages = sanitize_tool_messages(messages)
                if state.get("_task"):
                    state["_task"].record_compaction()
                self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})

            # [零进展救援轮] 本轮是「上一轮零产出」的强制重试，没有 tool_calls 可执行；
            # 追加空 tool_calls 的 assistant 消息是非法的，且会把上一轮 2.7 万字
            # reasoning 独白塞回上下文（双重浪费），故跳过。
            if cur_tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in cur_tool_calls
                    ],
                })
            elif (msg.content or "").strip():
                # 救援轮直接给出了正文（无工具）—— 追加以保留它，否则本轮内容会被
                # 随后的 LLM 调用覆盖掉，白花一次调用。
                messages.append({"role": "assistant", "content": msg.content})

            tool_tasks = []
            tool_metas = []
            early_results: dict[str, str] = {}
            # [工具参数截断守卫] finish_reason=length 且仍带 tool_calls 时，**参数一定是
            # 被输出上限砍断的**：`parse_tool_args` 会把半截 JSON「修复」成合法 dict，
            # 于是残缺字符串被当成完整内容执行 —— 实测「在 D 盘写个俄罗斯方块」写出
            # 24,877 字节的 tetris/game.js，结尾停在 `const dt = performance.now()`、
            # 括号不配平、无 IIFE 闭合，而 tool_write_file 回报成功。执行它等于静默
            # 产出损坏文件，比直接失败糟得多。这里一律不执行，改回一条明确错误让模型
            # 用更小的 payload 重试（分段写）。
            _args_truncated = str(finish_reason) == "length"
            for tc in cur_tool_calls:
                tool_name = tc.function.name
                # [token 优化 v5] 记录已使用工具 → 下轮重挂载时保留
                used_tools.add(tool_name)
                if _args_truncated:
                    early_results[tc.id] = _TRUNCATED_ARGS_ERROR.format(tool=tool_name)
                    self._push_event(state, {
                        "type": "tool_end", "step_id": f"tool_{tool_name}",
                        "name": f"调用工具: {tool_name}", "status": "error",
                        "tool_name": tool_name,
                        "tool_result": "参数因达到输出 token 上限被截断，已阻止执行",
                    })
                    continue
                args = parse_tool_args(tc.function.arguments)
                if args is None:
                    early_results[tc.id] = f"Error parsing arguments for '{tool_name}': 参数不是合法 JSON 且自动修复失败（已按空参数处理，请重新提交完整参数）"
                    continue

                # Dedup: 仅对只读且幂等的工具复用结果。
                # 非只读工具（写/执行）和非确定性工具（weather/时间/网络/HTTP 等）跳过缓存，
                # 避免同轮内相同参数第二次调用返回过期/陈旧结果。
                if not dedup.should_dedup(tool_name) or tool_name not in _DEDUP_READONLY_TOOLS:
                    self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    tool_tasks.append(self._execute_tool(tool_name, args, state))
                    tool_metas.append((tc.id, tool_name, None))
                    continue

                dedup_key = dedup.make_key(tool_name, args)
                cached = dedup.get(dedup_key)
                if cached is not None:
                    early_results[tc.id] = cached
                    self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": cached[:500]})
                    continue

                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tool_name, args, state))
                tool_metas.append((tc.id, tool_name, dedup_key))

            if tool_tasks:
                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
            else:
                tool_results = []

            for (tc_id, tool_name, dkey), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                result_str = str(result)
                if dkey is not None:
                    dedup.set(dkey, result_str)
                early_results[tc_id] = result_str

            # 变更类工具（写/编辑/执行/插件生成器等）执行后清空去重缓存，
            # 避免后续 read_file 命中旧缓存返回陈旧内容（写入→读取验证失效）
            if any(name not in _DEDUP_READONLY_TOOLS for _, name, _ in tool_metas):
                dedup.clear()

            for tc in cur_tool_calls:
                tc_id = tc.id
                result_str = early_results.get(tc_id, f"Error: no result for tool call {tc_id}")
                bounded_result = bound_tool_output(result_str, tc.function.name)
                tool_name = tc.function.name
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "tool_name": tool_name,
                    "content": bounded_result,
                })

            # TaskState 进度跟踪：每轮 +1 step，并按本轮工具调用数累加 tool_calls_count
            tool_calls_count += len(cur_tool_calls)
            if state.get("_task"):
                state["_task"].increment_step()
                state["_task"].increment_tool_calls(len(cur_tool_calls))

            # [C5 · 方案 D 基础] 每轮把执行进度落盘为 STEP_STATE（会话工作目录存在时），
            # 供长任务接力/断点续跑恢复；上下文只装摘要+当前步，旧轮次不再携带。
            # files 从本轮工具实参提取（写/改/删/生成器的真实路径），供下一步读取衔接。
            if state.get("_cwd") and rounds >= 1:
                from app.context.step_state import write_step_state
                done = [f"round {rounds}: {tc.function.name}" for tc in cur_tool_calls]
                write_step_state(
                    state["_cwd"], rounds,
                    {
                        "objective": state.get("question", ""),
                        "completed": done,
                        "active": ["等待下一轮工具调用或收尾总结"],
                        "blocked": [],
                        "next_move": ["继续剩余子任务；若接近上下文上限则先输出已完成部分"],
                        "files": self._extract_step_files(cur_tool_calls),
                    },
                )

            # [C5 · 方案 D 小步快走] 长任务周期性地把旧轮次压成摘要：
            # 上下文只装 [摘要 + 最近一轮 + 当前步]，不随轮次线性膨胀。
            if (
                settings.step_summary_enabled
                and rounds >= max(1, settings.step_summary_min_rounds)
                and rounds % max(1, settings.step_summary_interval) == 0
            ):
                messages = await self._step_summarize(messages, llm_call_budget(_model_hint))

            # Doom-loop 检测：同一组工具调用指纹连续重复 ≥ threshold 轮 → 注入策略变更提示；
            # 首次提示后仍连续重复（升级到 doom_loop_max_strikes）→ 强制收尾（注入 MAX_STEPS_PROMPT + 禁用工具）
            # 零进展救援轮没有 tool_calls → fp 为空串。若照样入指纹窗口，连续两轮
            # 救援的 "" 会被判成「重复工具调用」而误升级强制收尾，反而害死救援。
            fp = "|".join(
                sorted(f"{tc.function.name}:{tc.function.arguments}" for tc in cur_tool_calls)
            )
            if not fp:
                fp = None
            # 重复委派守卫：整轮只有 tool_task 且参数与本轮已发起过的完全一致 → 第 2 次即拦。
            # 比通用 doom-loop（阈值 3）更早，因为子 Agent 结果已在上下文里，重发必然无新信息。
            task_only = bool(cur_tool_calls) and all(
                tc.function.name == "tool_task" for tc in cur_tool_calls
            )
            if task_only and fp in delegated_tasks:
                repeat_delegation_strikes += 1
                logger.warning(
                    "Repeat delegation detected (%d): identical tool_task args re-sent (%s)",
                    repeat_delegation_strikes, fp[:120],
                )
                if repeat_delegation_strikes >= doom_max_strikes or steps_prompt_injected:
                    messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
                    steps_prompt_injected = True
                    self._push_event(state, {"type": "step_end", "step_id": "repeat_delegation", "name": "重复委派升级", "status": "completed", "detail": "已强制收尾总结"})
                else:
                    messages.append({"role": "user", "content": REPEAT_DELEGATION_PROMPT})
                    self._push_event(state, {"type": "step_end", "step_id": "repeat_delegation", "name": "检测到重复委派", "status": "completed", "detail": "已注入直接执行提示"})
            elif task_only:
                delegated_tasks.add(fp)

            # Doom-loop 指纹窗口只收有工具调用的轮；救援轮 fp=None 直接跳过。
            if fp is not None:
                doom_fingerprints.append(fp)
                # 首次按 threshold 判定；注入过策略提示后只要**再重复 1 次**就升级
                # 强制收尾（否则清空窗口要再等 threshold 轮，白烧几轮 LLM 调用）。
                window = doom_threshold if doom_strikes == 0 else 1
                if len(doom_fingerprints) >= window and len(set(doom_fingerprints[-window:])) == 1:
                    doom_strikes += 1
                    if doom_strikes >= doom_max_strikes:
                        logger.warning(
                            "Doom loop persisted (%d strikes), forcing structured summary: %s",
                            doom_strikes, fp[:120],
                        )
                        messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
                        steps_prompt_injected = True
                        self._push_event(state, {"type": "step_end", "step_id": "doom_loop", "name": "重复工具调用升级", "status": "completed", "detail": "已强制收尾总结"})
                    else:
                        logger.warning("Doom loop detected: %d consecutive identical tool calls (%s)", doom_threshold, fp[:120])
                        messages.append({"role": "user", "content": DOOM_LOOP_PROMPT})
                        self._push_event(state, {"type": "step_end", "step_id": "doom_loop", "name": "检测到重复工具调用", "status": "completed", "detail": "已注入策略变更提示"})
                    doom_fingerprints.clear()
                    if doom_strikes < doom_max_strikes:
                        doom_fingerprints.append(fp)  # 保留本轮指纹：再重复一次即升级

            # MAX_STEPS：达到生效上限前的最后一轮注入收尾提示（对齐 opencode prompt.ts:1281，
            # 以 assistant 角色消息注入，模型据此收尾总结）
            if not steps_prompt_injected and rounds >= effective_max_steps:
                steps_prompt_injected = True
                messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})

            # [C5 · G 前缀缓存] 冻结 tool_defs：请求内不再按 used_tools 每轮重挂载，
            # 使「system + tools」前缀跨轮次字节稳定 → DeepSeek 前缀缓存命中（0.1x 计费）。
            # 核心 tool_* 工具本就常驻 schema；模型若调用未挂载的插件/技能工具，_execute_tool
            # 仍会执行（self.tools 全量），仅本轮 schema 未列出该工具（下轮仍可被调用）。
            # [零进展救援已拿到正文] 直接收尾，不再多问一次（该轮已满足任务完成条件，
            # while 条件本也会因无 tool_calls 而退出，这里只是省掉一次 LLM 调用）。
            # 必须同时排除两种「假正文」，否则救援注入完立刻 break，模型永远拿不到那次重试：
            #   1) rescue_kind != zero_progress —— 超长内联正文档正文非空且已被判为残稿；
            #   2) _content_from_reasoning —— 正文其实是 core 从 reasoning_content 回退填进来的
            #      内心独白（实测 23,844 字符），`_is_zero_progress` 正是靠这个标记把它判成
            #      零进展的，这里若当正文收尾就等于自相矛盾。
            if (rescue_kind == "zero_progress" and not msg.tool_calls
                    and not getattr(msg, "_content_from_reasoning", False)
                    and (msg.content or "").strip()):
                self._push_event(state, {
                    "type": "step_end", "step_id": "zero_progress",
                    "name": "零进展救援", "status": "completed",
                    "detail": "强制产出后已获得有效回答",
                })
                break

            final_tool_defs = None if steps_prompt_injected else tool_defs
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(_model_hint), reserve_tokens=0, tool_defs=final_tool_defs))
            trace_messages("graph.round_ready", messages, tool_defs=final_tool_defs)  # [token trace v7]
            response = await self._llm_call(model, messages, final_tool_defs, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))

            # ── [零进展救援 / 超长内联正文救援] ──────────────────────────
            _maybe_rescue(msg, finish_reason, rounds)

        record_model_call(
            model, duration_ms=(tmod.time() - _gen_start) * 1000,
            tool_rounds=rounds, tool_calls=tool_calls_count,
        )
        trace("graph.finish", rounds=rounds, tool_calls=tool_calls_count, duration_ms=(tmod.time() - _gen_start) * 1000)  # [token trace v7]

        # If tool calls remain (max rounds reached) or content is empty, force a final answer
        if msg.tool_calls:
            logger.warning(
                "Max tool rounds reached (effective_max_steps=%d = min(MAX_STEPS=%d, MAX_TOOL_ROUNDS=%d)), "
                "executing final batch and forcing answer",
                effective_max_steps, settings.max_steps, max_tool_rounds,
            )
            # Must include tool_calls in assistant message for DeepSeek/OpenAI compatibility
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            tool_tasks = []
            tool_metas = []
            for tc in msg.tool_calls:
                args = parse_tool_args(tc.function.arguments)
                if args is None:
                    args = {}
                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tc.function.name}", "name": f"调用工具: {tc.function.name}", "status": "running", "tool_name": tc.function.name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tc.function.name, args, state))
                tool_metas.append((tc.id, tc.function.name))

            tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)

            for (tc_id, tool_name), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                result_str = str(result)
                bounded_result = bound_tool_output(result_str, tool_name)
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({"role": "tool", "tool_call_id": tc_id, "tool_name": tool_name, "content": bounded_result})
                used_tools.add(tool_name)
            # [token 优化 P8] 强制收尾路径补齐"清理→压缩→截断"闭环：此前仅截断，
            # 且截断基于低估估算可能不触发（实测收尾轮裸发 25,779 超 usable 23,808）。
            # 与主循环保持同款处理，避免收尾调用成为单请求内最大单次 pt。
            trace_messages("graph.final_round_start", messages, tool_defs=None)  # [token trace v8]
            messages = prune_tool_outputs(
                messages,
                protect_tokens=prune_protect_tokens(_model_hint),
                minimum_tokens=prune_minimum_tokens(_model_hint),
                tail_turns=settings.context_tail_turns,
            )
            if compactor.should_compact(messages):
                self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
                old_count = len(messages)
                messages = await compactor.compact(messages)
                messages = sanitize_tool_messages(messages)
                if state.get("_task"):
                    state["_task"].record_compaction()
                self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})
            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=llm_call_budget(_model_hint), reserve_tokens=0))
            trace_messages("graph.final_round_ready", messages, tool_defs=None)  # [token trace v8]
            # 对齐 opencode max-steps 语义：达到上限后工具禁用，仅注入收尾总结提示（assistant 角色）
            messages.append({"role": "assistant", "content": MAX_STEPS_PROMPT})
            response = await self._llm_call(model, messages, None, state=state)
            msg = response.choices[0].message
            finish_reason = _normalize_finish_reason(getattr(response.choices[0], "finish_reason", None))
        if msg.tool_calls:
            # 收尾调用已禁用工具（tools=None），理论上不应返回 tool_calls；
            # 若模型仍输出，其 tool_calls 不会进入 answer，仅告警记录便于排查
            logger.warning(
                "Forced final LLM call returned %d tool_calls despite tools disabled (rounds=%d)",
                len(msg.tool_calls), rounds,
            )
        # [Ollama 流式兼容兜底] 最终回答若仍残留工具调用 JSON 文本（前一环重组失败或
        # 末轮工具禁用后模型仍照例输出），剥除/抽取内嵌回复，避免把 JSON 直接展示。
        from .core import _sanitize_tool_call_content, _parse_streamed_tool_call
        cleaned = _sanitize_tool_call_content(msg.content, None)
        if cleaned is not None and cleaned != msg.content:
            msg.content = cleaned
        elif _parse_streamed_tool_call(msg.content) is not None:
            # 纯工具声明 JSON 残留（无内嵌回复字段）→ 丢弃，交由下方兜底文案
            msg.content = ""
        # [Ollama 本地模型兜底] 弱模型（qwen2.5-coder / mistral 等）常把整个回答包在
        # {"response":"..."} 或 {"role":"assistant","content":"..."} JSON 外壳里——
        # 统一解包提取纯文本，避免用户看到 JSON 原文。
        from app.utils.json_repair import parse_answer_envelope
        if msg.content:
            msg.content = parse_answer_envelope(msg.content)
        # [弱模型鲁棒性] 强模型空回答 → 同模型纯重试一次；弱模型留空，交由 `_generate`
        # 用强模型**完整重跑**（含工具循环）——避免拿 tools=None 的残缺上下文问强模型。
        if settings.empty_answer_retry and not _is_valid_answer(msg.content):
            msg.content = await self._retry_empty_answer(messages, model, state)

        # P4: finish_reason 收尾语义（对齐 opencode prompt.ts:1301-1308 / processor.ts）
        # length → 输出被截断，答案不完整，追加提示不静默
        # content-filter → 内容被 Provider 过滤，视为错误暴露
        answer = msg.content or ""
        gen_dur = (tmod.time() - _gen_start) * 1000
        # [零进展] 最后一轮既没发 tool_call 也没有**模型自己写的**正文 → 本次请求
        # 没做成任何事。典型是思考模型把输出预算全烧在 reasoning 上
        # （单轮 ct == reasoning == 8192），content 空 + finish=length，循环随即退出。
        # 实测「写 React 俄罗斯方块」：第二轮即此情形，D:\game 零文件，
        # 用户却收到 27908 字内心独白（core 的 reasoning 回退把零产出伪装成回答）。
        # 注意不能用 `rounds == 0` 判定：实测该场景 rounds == 1（首轮确实调过
        # tool_ls/tool_execute），真正该看的是**最后一轮**有没有产出。
        # `msg._content_from_reasoning` 由 core 在 reasoning 回退前标记，
        # 用来区分「真截断的完整回答」与「思考耗尽预算的内心独白」。
        _from_reasoning = bool(getattr(msg, "_content_from_reasoning", False))
        zero_progress = self._is_zero_progress(msg)
        if zero_progress:
            _u = self._usage_accum
            self._chainlog_zero_progress(
                state=state, model=model, finish_reason=finish_reason,
                rounds=rounds,
                input_tokens=int((_u or {}).get("input", 0)),
                output_tokens=int((_u or {}).get("output", 0)),
                reasoning_tokens=int((_u or {}).get("reasoning", 0)),
                cost=round(float(getattr(self, "_cost_accum", 0.0)), 6),
                answer_chars=len(answer),
                from_reasoning=_from_reasoning,
            )
            # 救援也没能把模型拉回正轨：此时 `answer` 是 core 回退填进来的
            # reasoning_content —— 实测 2.7 万字内心独白。直接返回它既撑爆下一轮
            # 上下文预算，又让用户以为「模型写了代码」而实际磁盘零文件。
            # 换成可诊断的失败文案（保留零进展日志作为排查线索）。
            if _from_reasoning and settings.zero_progress_mask_reasoning:
                answer = (
                    "**本次请求未能产出任何内容。**\n\n"
                    f"模型 `{model}` 连续 {rounds + 1} 轮把全部输出预算耗尽在内部思考上，"
                    "既没有调用任何工具，也没有输出正文。\n\n"
                    "已自动重试 "
                    f"{rescue_used} 次仍未成功。常见原因与建议：\n"
                    "- 该模型的思考预算与本项目输出上限（`LLM_MAX_TOKENS`）不匹配，"
                    "思考阶段就把配额用光了 → 调高 `LLM_MAX_TOKENS` 或改用思考较浅的模型；\n"
                    "- 任务单轮产出过大（整份代码写进一次回复）→ 拆成多个小步骤，"
                    "或要求模型用写文件工具分批落盘。\n\n"
                    "排查线索：全链路日志中搜索 `generate.zero_progress`。"
                )
        if zero_progress:
            # 零进展：既没调工具也没给出有效回答，不能报「完成」骗用户
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "error", "detail": f"零进展（finish={finish_reason}，思考耗尽输出预算，未执行任何工具，已重试 {rescue_used} 次）", "duration_ms": round(gen_dur, 1)})
        elif finish_reason == "length":
            answer = answer + "\n\n⚠️ 输出因达到 token 上限被截断，内容可能不完整。"
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "completed", "detail": "完成（输出被截断 length）", "duration_ms": round(gen_dur, 1)})
        elif finish_reason == "content-filter":
            answer = "模型回答被内容安全策略拦截，未返回完整内容。请调整提问方式或拆分内容后重试。"
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "error", "detail": "内容被过滤", "duration_ms": round(gen_dur, 1)})
        else:
            self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "completed", "detail": f"完成（{rounds} 轮工具调用）" if rounds else "完成", "duration_ms": round(gen_dur, 1)})
        # [token 优化 v15] 本请求实际用到的工具并入会话缓存 → 同会话下一请求 schema 自动带上，
        # 前缀仅在尾部增长，DeepSeek 前缀缓存跨请求继续命中
        self._remember_conversation_tools(state.get("conversation_id", ""), used_tools)
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
            "model": model,
            "finish": finish_reason,
            "tokens": dict(self._usage_accum),
            "cost": round(float(getattr(self, "_cost_accum", 0.0)), 6),
        }

    @staticmethod
    def _is_zero_progress(msg) -> bool:
        """本轮是否「什么都没产出」。

        判据不能只看 `msg.content` 是否为空 —— `core._llm_call` 在 content 为空时会把
        `reasoning_content` 回退填进去（防思考模型答空），于是「思考耗尽预算、零产出」
        会被伪装成「有回答」。`_content_from_reasoning` 是回退**之前**打的标记，
        据此把两者分开。
        """
        if getattr(msg, "tool_calls", None):
            return False
        if bool(getattr(msg, "_content_from_reasoning", False)):
            return True
        return not (getattr(msg, "content", "") or "").strip()

    @staticmethod
    def _is_oversized_prose(msg, finish_reason) -> bool:
        """本轮是否是「超长内联正文 + 零工具调用 + 被截断」。

        与 `_is_zero_progress` 互补而非替代：那一档判定「什么都没做」，这一档判定
        「做了，但做在聊天窗口里而非磁盘上」。实测 deepseek-v4-flash 无视系统提示里
        「长内容写文件」的规则，把 8192 输出预算全写成 24K 字聊天正文，`tool_calls=[]`
        + `finish_reason=length` → while 条件退出 → 磁盘零文件、用户收到腰斩的规划稿。

        以 `finish_reason == "length"` 作为「被截断」的硬证据（正文非空却撞上限，用户
        拿到的必然是残缺回答），再用 `OVERSIZED_PROSE_MIN_CHARS` 过滤「短回答恰好被截」
        的偶发情形，避免对正常短问答多问一轮。
        """
        if getattr(msg, "tool_calls", None):
            return False
        if str(finish_reason) != "length":
            return False
        return len(getattr(msg, "content", "") or "") >= OVERSIZED_PROSE_MIN_CHARS

    def _chainlog_zero_progress(
        self, *, state, model: str, finish_reason: str, rounds: int,
        input_tokens: int, output_tokens: int, reasoning_tokens: int,
        cost: float, answer_chars: int, from_reasoning: bool = False,
        event: str = "generate.zero_progress",
    ) -> None:
        """记一条「零进展」全链路日志节点（stage=agent, level=ERROR）。

        单独成节点而不是只塞进 llm 节点的 data：日志页默认按时间线展示，
        一条 ERROR 节点能被一眼扫到；而 llm 节点里的 no_progress 标记要展开
        data 才看得见。
        """
        try:
            from app import chainlog

            agent_id = ""
            if state is not None:
                agent_id = str(state.get("agent") or state.get("agent_id") or "")
            chainlog.log(
                "ERROR", "agent", "graph.generate",
                event,
                message=(
                    f"零进展：{model} finish={finish_reason}，"
                    f"输出 {output_tokens} token 全被思考占用，未执行任何工具"
                ),
                agent_id=agent_id,
                data={
                    "model": model,
                    "finish_reason": str(finish_reason),
                    "rounds": rounds,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "reasoning_ratio": round(reasoning_tokens / output_tokens, 3) if output_tokens else 0,
                    "cost": cost,
                    "answer_chars": answer_chars,
                    "tools_invoked": 0,
                    "answer_from_reasoning": bool(from_reasoning),
                    # 聚合口径下 reasoning 不会精确等于 output（含首轮正常轮次），
                    # 故用 0.9 阈值而非 >=：实测 8500/8670 = 0.98 才是同一现象
                    "cause": "reasoning_budget_exhausted"
                             if output_tokens > 0 and reasoning_tokens >= output_tokens * 0.9
                             else "length_without_tool_calls",
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def _resolve_fallback_model(self, model: str) -> str:
        """回退模型优先级：显式 env（EMPTY_ANSWER_FALLBACK_MODEL_NAME）→ 前端「模型管理」默认
        模型（catalog.default_model()）→ .env 的 LLM_MODEL（self.model）。"""
        try:
            from app.models.catalog import default_model as _catalog_default_model
            catalog_default = _catalog_default_model() or ""
        except Exception:  # noqa: BLE001
            catalog_default = ""
        return (
            (settings.empty_answer_fallback_model_name or "").strip()
            or catalog_default
            or self.model
        )

    async def _retry_empty_answer(self, messages: list[dict], model: str, state) -> str:
        """[弱模型鲁棒性] 空回答（含 {}）自动重试。

        - **弱模型**：直接返回 ""，交由 `_generate` 用强模型**完整重跑**（含工具循环）。
          （旧实现拿 tools=None 的残缺上下文问强模型，会诱发强模型把工具调用当文本输出。）
        - **强模型**：同模型**纯重试**一次（偶发空输出），仍失败返回 ""。
        """
        from app.utils.json_repair import parse_answer_envelope

        if is_weak_model(model):
            return ""
        try:
            resp = await self._llm_call(model, list(messages), None, state=state)
            text = parse_answer_envelope((resp.choices[0].message.content or "").strip())
            if _is_valid_answer(text):
                logger.info("empty-answer recovered via model=%s", model)
                return text
        except Exception as e:  # noqa: BLE001
            logger.warning("empty-answer retry via %s failed: %s", model, e)
        return ""

__all__ = ['RAGAgentGenerate']

