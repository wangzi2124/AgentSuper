"""Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future
"""

import asyncio
import json
import logging
import time as tmod
import uuid
from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.memory import MemoryManager
from app.config import settings
from app.monitor import record_model_call

logger = logging.getLogger(__name__)

# ── [token 优化 v4] 多 Agent 汇总截断：子 Agent 完整答案已直通用户，汇总只需要点 ──
SUB_RESULT_TRUNC = 3000  # 字符

# ── 分解提示词 ──
DECOMPOSE_SYSTEM_PROMPT = """你是一个任务分解专家。将用户的复杂问题拆解成多个可以并行执行的子任务。

当前可用的 Agent:
  - "rag":     知识库检索 Agent（处理文档、小说、角色、对话、知识库相关问题）
  - "web_search": 网络搜索 Agent（处理实时信息、新闻、网络资源相关问题）
  - "code":    代码 Agent（处理编程、代码编写、代码审查相关问题）

要求:
1. 每个子任务只指定一个 Agent
2. 子任务之间不要有依赖关系（可以并行执行）
3. 每个子任务有清晰的描述
4. 如果问题很简单，只需要一个 Agent，就只返回一个子任务
5. 最多拆成 3 个子任务

输出格式（纯 JSON 数组，不要 markdown 标记）:
[
  {"agent": "rag", "question": "原问题中需要知识库的部分"},
  {"agent": "web_search", "question": "原问题中需要网络搜索的部分"},
  {"agent": "code", "question": "原问题中需要代码的部分"}
]
"""

SYNTHESIS_SYSTEM_PROMPT = """你是信息汇总专家。以下是多个并行搜索结果，请将它们整合成一个连贯、完整的回答。

要求:
- 合并信息，去除重复内容
- 按逻辑顺序（而非 Agent 顺序）组织内容
- 如果某个 Agent 返回了错误，忽略它并基于其他结果回答
- 使用中文回答
- 在回答末尾标注信息来源（如 [知识库]、[网络搜索]、[代码分析]）"""


class SupervisorAgent(BaseAgent):
    """Supervisor Agent —— 路由和任务分解。"""

    # 可路由的 Agent 白名单（排除 supervisor 自身，防止 LLM 返回 "supervisor" 造成自我递归）
    ROUTABLE_AGENTS = {"rag", "web_search", "code"}

    def __init__(self, bus: AgentBus, memory: Optional[MemoryManager] = None):
        self._bus = bus
        self._memory = memory
        self._id = "supervisor"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base
        # 使用更长超时的子 Agent（工具密集型，如 code）
        self._extended_timeout_agents = {
            a.strip() for a in (settings.extended_timeout_agents or "").split(",") if a.strip()
        }

    def _timeout_for(self, agent_id: str) -> float:
        """按 Agent 类型分级超时：工具密集型 Agent 使用更长等待，避免长任务被误判超时。"""
        if agent_id in self._extended_timeout_agents:
            return settings.sub_agent_timeout_extended
        return settings.sub_agent_timeout

    @property
    def agent_id(self) -> str:
        return self._id

    # ═══════════════════════════════════════════════════════════════
    #  Handle Message （入口）
    # ═══════════════════════════════════════════════════════════════

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        if action == "chat":
            question = payload.get("question", "")

            # [token 优化 v9] 本次请求的 LLM 用量汇总（分解 + 子 Agent + 汇总），
            # 随 response payload 落库，与单 Agent executor 口径对齐。
            # 注：bus 事件循环对每个 agent 串行处理消息，无并发写冲突。
            self._usage = {"input": 0, "output": 0}

            # ── 尝试任务分解 ──
            subtasks = await self._decompose(question)

            # 安全护栏：只路由到白名单 Agent，防止 LLM 返回 "supervisor" 造成自我递归超时
            subtasks = [st for st in subtasks if st.get("agent") in self.ROUTABLE_AGENTS]
            if not subtasks:
                subtasks = [{"agent": "rag", "question": question}]

            if len(subtasks) > 1:
                logger.info(
                    "Supervisor decomposed into %d subtasks (thread=%s)",
                    len(subtasks), msg.thread_id,
                )
                # 并行执行分解后的子任务
                result = await self._execute_parallel(subtasks, payload, msg.thread_id)
                yield result
            else:
                # 只有一个子任务 → 走简单路由
                target_agent = subtasks[0]["agent"] if subtasks else "rag"
                logger.info(
                    "Supervisor routing to '%s' (thread=%s)",
                    target_agent, msg.thread_id,
                )
                async for reply in self._route_to(target_agent, payload, msg.thread_id):
                    yield reply

        else:
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": f"Supervisor doesn't support action: {action}"},
                thread_id=msg.thread_id,
            )

    # ═══════════════════════════════════════════════════════════════
    #  Bug 修复: 使用独立 thread_id 发送子请求
  # ═══════════════════════════════════════════════════════════════

    async def _route_to(
        self,
        target_agent: str,
        payload: dict,
        original_thread_id: str,
    ) -> AsyncIterator[AgentMessage]:
        """转发到目标 Agent 并等待回复。

        🔧 Bug 修复: 子请求使用独立 thread_id，避免覆盖调用方的 Future。
        """
        sub_thread_id = f"{original_thread_id}:sub:{uuid.uuid4().hex[:8]}"
        timeout = self._timeout_for(target_agent)

        try:
            reply = await self._bus.send_and_wait(
                AgentMessage(
                    source=self._id,
                    target=target_agent,
                    type="request",
                    action="chat",
                    payload=payload,
                    thread_id=sub_thread_id,  # 🔧 独立 thread_id
                ),
                timeout=timeout,
            )

            if reply.type == "response":
                # [token 优化 v9] 子 Agent 用量计入本次请求汇总
                if getattr(self, "_usage", None) is not None:
                    _tk = reply.payload.get("tokens") or {}
                    self._usage["input"] += _tk.get("input", 0)
                    self._usage["output"] += _tk.get("output", 0)
                yield AgentMessage(
                    source=self._id,
                    target="user",  # 由 bus.send 路由回 original 的调用者
                    type="response",
                    action="chat",
                    payload={
                        **reply.payload,
                        "routed_to": target_agent,
                        "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                    },
                    thread_id=original_thread_id,  # 🔧 使用原始 thread_id 回复
                )
            elif reply.type == "error":
                # bus 现在以 AgentMessage(type="error") 交付子 Agent 错误，
                # 透传 error payload（含 completed_steps 等上下文）。
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={
                        "error": reply.payload.get("error", "Sub-agent failed"),
                        "error_type": reply.payload.get("error_type", "sub_agent_error"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    },
                    thread_id=original_thread_id,
                )
            else:
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={"error": f"Sub-agent returned unexpected type: {reply.type}"},
                    thread_id=original_thread_id,
                )

        except asyncio.TimeoutError:
            logger.warning("Sub-agent '%s' timed out after %.0fs (thread=%s)", target_agent, timeout, original_thread_id)
            completed = self._bus.agent_progress(target_agent)
            suggestion = (
                f"如果任务仍在执行（如代码脚手架/构建），可提高 SUB_AGENT_TIMEOUT "
                f"或 SUB_AGENT_TIMEOUT_EXTENDED，或改用普通对话模式重试。"
            )
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": (
                        f"Agent '{target_agent}' did not respond in time (waited {timeout:.0f}s). "
                        f"已完成步骤: {(' → '.join(completed) if completed else '无可获取的处理进度')}. "
                        f"{suggestion}"
                    ),
                    "error_type": "sub_agent_timeout",
                    "timeout": timeout,
                    "completed_steps": completed,
                    "suggestion": suggestion,
                },
                thread_id=original_thread_id,
            )
        except Exception as e:
            logger.exception("Supervisor error routing to %s", target_agent)
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": str(e),
                    "error_type": "sub_agent_error",
                },
                thread_id=original_thread_id,
            )

    # ═══════════════════════════════════════════════════════════════
    #  任务分解
    # ═══════════════════════════════════════════════════════════════

    async def _decompose(self, question: str) -> list[dict]:
        """将复杂问题拆解成多个子任务。

        返回格式: [{"agent": "rag", "question": "..."}, ...]
        """
        q = question.strip().lower()

        # ── 快速路径: 关键词 + 可用 Agent 判断 ──
        available_agents = [a for a in self._bus.list_agents() if a in self.ROUTABLE_AGENTS]

        kb_keywords = [
            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",
            "人物", "情节", "书中", "记载", "来源", "character", "dialogue",
            "novel", "chapter", "story",
            # [token 优化] 扩充
            "摘要", "总结", "作者", "主角", "配角", "人物关系", "出场", "设定",
            "世界观", "结局", "大意", "简介", "summary", "author", "plot",
        ]
        code_keywords = [
            "代码", "编程", "函数", "bug", "debug", "程序", "算法",
            "python", "javascript", "typescript", "前端", "后端",
            "code", "function", "programming",
            # [token 优化] 扩充
            "脚本", "接口", "api", "报错", "异常", "重构", "依赖", "配置",
            "测试", "部署", "数据库", "sql", "react", "vue", "node", "docker", "git",
        ]
        web_keywords = [
            "新闻", "最新", "天气", "搜索", "查找", "实时",
            "news", "weather", "search", "latest", "today",
            # [token 优化] 扩充
            "热搜", "公告", "发布", "汇率", "股价", "比赛", "比分", "排行榜",
            "政策", "法规", "通知", "announcement", "release",
        ]

        needs_kb = any(kw in q for kw in kb_keywords) and "rag" in available_agents
        needs_code = any(kw in q for kw in code_keywords) and "code" in available_agents
        needs_web = any(kw in q for kw in web_keywords) and "web_search" in available_agents

        # 如果关键词匹配到多个，尝试 LLM 分解
        if (needs_kb and needs_code) or (needs_kb and needs_web) or (needs_code and needs_web):
            return await self._llm_decompose(question, available_agents)

        # 单一明确意图
        if needs_code:
            return [{"agent": "code", "question": question}]
        if needs_web:
            return [{"agent": "web_search", "question": question}]
        if needs_kb:
            return [{"agent": "rag", "question": question}]

        # ── [token 优化] 简短问题(≤24字符)直接走 rag,免 LLM 分解 ──
        if len(question.strip()) <= 24:
            return [{"agent": "rag", "question": question}]

        # ── 默认: 尝试 LLM 分解 ──
        return await self._llm_decompose(question, available_agents)

    async def _llm_decompose(self, question: str, available: list[str]) -> list[dict]:
        """使用 LLM 判断如何分解任务。

        - 输出先做 JSON 解析 + schema 校验（agent 必须在白名单且可用、question 非空）
        - 解析/校验失败时带错误信息与格式样例做一次 few-shot 修复重试
        - 仍失败才回退 rag（记录原因，便于排查路由漂移）
        """
        routable = [a for a in available if a in self.ROUTABLE_AGENTS] or ["rag"]

        async def _request(messages: list[dict]) -> tuple[str, dict]:
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=messages,
                max_tokens=512,
                temperature=0.1,
            )
            usage = getattr(response, "usage", None)
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            }
            # [token 优化 v9] 分解调用的用量计入本次请求汇总
            if getattr(self, "_usage", None) is not None:
                self._usage["input"] += usage_dict.get("prompt_tokens", 0)
                self._usage["output"] += usage_dict.get("completion_tokens", 0)
            return response.choices[0].message.content, usage_dict

        start = tmod.time()
        attempts = []
        for attempt in range(1):  # [token 优化] 分解失败重试 2→1,失败直接回退 rag
            try:
                if attempt == 0:
                    messages = [
                        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"可用的 Agent: {', '.join(routable)}\n\n用户问题: {question}"},
                    ]
                else:
                    # few-shot 修复：带上一次的错误与合法格式样例
                    messages = [
                        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"可用的 Agent: {', '.join(routable)}\n\n用户问题: {question}"},
                        {
                            "role": "assistant",
                            "content": "抱歉，我需要先输出子任务分解。",
                        },
                        {
                            "role": "user",
                            "content": (
                                "你上一次的输出无法解析，原因如下：\n"
                                f"{attempts[-1]}\n\n"
                                "请严格按照以下 JSON 数组格式重新输出（不要 markdown 代码块标记），"
                                "且 agent 字段只能取 " + ", ".join(routable) + "：\n"
                                '[\n  {"agent": "rag", "question": "第一个子任务的问题描述"},\n'
                                '  {"agent": "web_search", "question": "第二个子任务的问题描述"}\n]\n'
                            ),
                        },
                    ]
                text, usage = await _request(messages)
                if attempt == 0:
                    dur = (tmod.time() - start) * 1000
                    record_model_call(
                        self._model,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        duration_ms=dur,
                    )
                text = text.strip()
                text = text.replace("```json", "").replace("```", "").strip()
                subtasks = json.loads(text)
                validated = self._validate_subtasks(subtasks, routable)
                if validated:
                    return validated
                attempts.append("schema 校验未通过：返回了空/非法的子任务列表")
            except Exception as e:  # noqa: BLE001
                attempts.append(f"{type(e).__name__}: {e}")

        logger.warning(
            "LLM decomposition failed after %d attempt(s): %s; falling back to rag",
            len(attempts), attempts[-1] if attempts else "unknown",
        )
        return [{"agent": "rag", "question": question}]

    @staticmethod
    def _validate_subtasks(data, routable: list[str]) -> list[dict]:
        """校验并规范化 LLM 分解输出，返回合法子任务列表（白名单过滤 + 最多 3 个）。"""
        if not isinstance(data, list):
            return []
        validated: list[dict] = []
        for st in data:
            if not isinstance(st, dict):
                continue
            agent = st.get("agent")
            q = st.get("question")
            if not isinstance(agent, str) or not isinstance(q, str) or not q.strip():
                continue
            if agent in routable:
                validated.append({"agent": agent, "question": q.strip()})
            if len(validated) >= 3:
                break
        return validated

    # ═══════════════════════════════════════════════════════════════
    #  并行执行
    # ═══════════════════════════════════════════════════════════════

    async def _execute_parallel(
        self,
        subtasks: list[dict],
        original_payload: dict,
        original_thread_id: str,
    ) -> AgentMessage:
        """并行执行多个子任务并汇总结果。"""
        results: list[dict] = []
        errors: list[str] = []

        async def run_one(st: dict) -> dict:
            """执行单个子任务。"""
            sub_thread_id = f"{original_thread_id}:decomp:{uuid.uuid4().hex[:8]}"
            timeout = self._timeout_for(st["agent"])
            try:
                # 构建子任务 payload（继承原始 payload，但 question 用子任务的）
                sub_payload = dict(original_payload)
                sub_payload["question"] = st.get("question", original_payload.get("question", ""))

                reply = await self._bus.send_and_wait(
                    AgentMessage(
                        source=self._id,
                        target=st["agent"],
                        type="request",
                        action="chat",
                        payload=sub_payload,
                        thread_id=sub_thread_id,
                    ),
                    timeout=timeout,
                )
                if reply.type == "error":
                    return {
                        "agent": st["agent"],
                        "original_question": st["question"],
                        "answer": "",
                        "error": reply.payload.get("error", "Sub-agent failed"),
                        "error_type": reply.payload.get("error_type", "sub_agent_error"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    }
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": reply.payload.get("answer", ""),
                    "sources": reply.payload.get("sources", []),
                    # [token 优化 v9] 透传子 Agent 用量，便于汇总
                    "tokens": reply.payload.get("tokens") or {},
                }
            except asyncio.TimeoutError:
                completed = self._bus.agent_progress(st["agent"])
                suggestion = (
                    f"如果任务仍在执行（如代码脚手架/构建），可提高 SUB_AGENT_TIMEOUT "
                    f"或 SUB_AGENT_TIMEOUT_EXTENDED，或改用普通对话模式重试。"
                )
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": (
                        f"Agent '{st['agent']}' did not respond in time (waited {timeout:.0f}s). "
                        f"已完成步骤: {(' → '.join(completed) if completed else '无可获取的处理进度')}."
                    ),
                    "error_type": "sub_agent_timeout",
                    "completed_steps": completed,
                    "suggestion": suggestion,
                }
            except Exception as e:
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": str(e),
                    "error_type": "sub_agent_error",
                }

        # 并行执行所有子任务
        task_results = await asyncio.gather(*[run_one(st) for st in subtasks])

        # [token 优化 v9] 各子 Agent 用量计入本次请求汇总
        if getattr(self, "_usage", None) is not None:
            for r in task_results:
                _tk = r.get("tokens") or {}
                self._usage["input"] += _tk.get("input", 0)
                self._usage["output"] += _tk.get("output", 0)

        for r in task_results:
            if r.get("error"):
                note = f"[{r['agent']}] {r['error']}"
                cs = r.get("completed_steps") or []
                if cs:
                    note += f" 已完成: {' → '.join(cs)}"
                errors.append(note)
            else:
                results.append(r)

        # 存入记忆（按 conversation 隔离）
        if self._memory:
            conv_id = original_payload.get("conversation_id", "")
            await self._memory.set(
                f"decomposed_results",
                {
                    "subtasks": subtasks,
                    "results": results,
                    "errors": errors,
                },
                ttl=300,
                tags=["supervisor", "decomposition"],
                namespace=conv_id,  # 🔒 Session 隔离
            )

        # 合成最终回答
        if len(results) == 1:
            # 只有一个成功的结果
            r = results[0]
            return AgentMessage(
                source=self._id, target="user",
                type="response", action="chat",
                payload={
                    "answer": r["answer"],
                    "sources": r.get("sources", []),
                    "steps": [],
                    "routed_to": r["agent"],
                    "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                },
                thread_id=original_thread_id,
            )
        else:
            # 多个结果 → 合成
            answer = await self._synthesize(question=original_payload.get("question", ""), results=results)
            all_sources = []
            for r in results:
                all_sources.extend(r.get("sources", []))

            error_note = ""
            if errors:
                error_note = f"\n\n⚠️ 部分 Agent 执行出错: {'; '.join(errors)}"

            return AgentMessage(
                source=self._id, target="user",
                type="response", action="chat",
                payload={
                    "answer": answer + error_note,
                    "sources": all_sources,
                    "steps": [],
                    "routed_to": "+".join(r["agent"] for r in results),
                    "tokens": dict(getattr(self, "_usage", {"input": 0, "output": 0})),
                },
                thread_id=original_thread_id,
            )

    async def _synthesize(
        self,
        question: str,
        results: list[dict],
    ) -> str:
        """将多个并行结果合成为一个回答。"""
        if not results:
            return "抱歉，所有 Agent 都未能返回结果。"

        segments = []
        for i, r in enumerate(results):
            agent_label = {"rag": "知识库", "web_search": "网络搜索", "code": "代码分析"}.get(r["agent"], r["agent"])
            # [token 优化 v4] 子 Agent 结果超长时截断，避免多结果汇总输入膨胀
            # （完整答案已由单 Agent 路由直接返回给用户；汇总仅需其要点）
            answer = r.get("answer", "")
            if len(answer) > SUB_RESULT_TRUNC:
                answer = answer[:SUB_RESULT_TRUNC] + f"\n…[子 Agent 结果过长，已截断前 {SUB_RESULT_TRUNC} 字符]"
            segments.append(
                f"[{agent_label} — {r.get('original_question', '')[:50]}]\n{answer}"
            )

        context = "\n\n---\n\n".join(segments)

        try:
            start = tmod.time()
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": f"用户问题: {question}\n\n各 Agent 返回:\n{context}"},
                ],
                max_tokens=2048,
                temperature=0.3,
            )
            dur = (tmod.time() - start) * 1000
            usage = getattr(response, "usage", None)
            pt = getattr(usage, "prompt_tokens", 0) if usage else 0
            ct = getattr(usage, "completion_tokens", 0) if usage else 0
            # [token 优化 v9] 汇总调用的用量计入本次请求
            if getattr(self, "_usage", None) is not None:
                self._usage["input"] += pt
                self._usage["output"] += ct
            record_model_call(self._model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            # 回退：拼接结果
            lines = [f"以下是多个来源的信息汇总:\n"]
            for r in results:
                lines.append(f"--- {r['agent']} ---")
                lines.append(r.get("answer", "（无结果）"))
            return "\n".join(lines)
