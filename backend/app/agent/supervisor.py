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
import logging
import uuid
from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.memory import MemoryManager
from app.config import settings

logger = logging.getLogger(__name__)

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

    def __init__(self, bus: AgentBus, memory: Optional[MemoryManager] = None):
        self._bus = bus
        self._memory = memory
        self._id = "supervisor"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base

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

            # ── 尝试任务分解 ──
            subtasks = await self._decompose(question)

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
                timeout=60.0,
            )

            if reply.type == "response":
                yield AgentMessage(
                    source=self._id,
                    target="user",  # 由 bus.send 路由回 original 的调用者
                    type="response",
                    action="chat",
                    payload={
                        **reply.payload,
                        "routed_to": target_agent,
                    },
                    thread_id=original_thread_id,  # 🔧 使用原始 thread_id 回复
                )
            else:
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={"error": f"Sub-agent returned unexpected type: {reply.type}"},
                    thread_id=original_thread_id,
                )

        except asyncio.TimeoutError:
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": f"Agent '{target_agent}' did not respond in time"},
                thread_id=original_thread_id,
            )
        except Exception as e:
            logger.exception("Supervisor error routing to %s", target_agent)
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": str(e)},
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
        available_agents = self._bus.list_agents()

        kb_keywords = [
            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",
            "人物", "情节", "书中", "记载", "来源", "character", "dialogue",
            "novel", "chapter", "story",
        ]
        code_keywords = [
            "代码", "编程", "函数", "bug", "debug", "程序", "算法",
            "python", "javascript", "typescript", "前端", "后端",
            "code", "function", "programming",
        ]
        web_keywords = [
            "新闻", "最新", "天气", "搜索", "查找", "实时",
            "news", "weather", "search", "latest", "today",
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

        # ── 默认: 尝试 LLM 分解 ──
        return await self._llm_decompose(question, available_agents)

    async def _llm_decompose(self, question: str, available: list[str]) -> list[dict]:
        """使用 LLM 判断如何分解任务。"""
        try:
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=[
                    {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"可用的 Agent: {', '.join(available)}\n\n用户问题: {question}"},
                ],
                max_tokens=512,
                temperature=0.1,
            )

            text = response.choices[0].message.content.strip()
            # 移除可能的 markdown 代码块标记
            text = text.replace("```json", "").replace("```", "").strip()
            subtasks = __import__("json").loads(text)

            # 验证格式
            validated = []
            for st in subtasks:
                if isinstance(st, dict) and "agent" in st and "question" in st:
                    if st["agent"] in available:
                        validated.append(st)

            if validated:
                return validated

        except Exception as e:
            logger.warning("LLM decomposition failed: %s, falling back to rag", e)

        # Fallback
        return [{"agent": "rag", "question": question}]

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
                    timeout=60.0,
                )
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": reply.payload.get("answer", ""),
                    "sources": reply.payload.get("sources", []),
                }
            except asyncio.TimeoutError:
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": f"Agent '{st['agent']}' did not respond in time",
                }
            except Exception as e:
                return {
                    "agent": st["agent"],
                    "original_question": st["question"],
                    "answer": "",
                    "error": str(e),
                }

        # 并行执行所有子任务
        task_results = await asyncio.gather(*[run_one(st) for st in subtasks])

        for r in task_results:
            if r.get("error"):
                errors.append(f"[{r['agent']}] {r['error']}")
            else:
                results.append(r)

        # 存入记忆
        if self._memory:
            await self._memory.set(
                f"decomposed_{original_thread_id[:16]}",
                {
                    "subtasks": subtasks,
                    "results": results,
                    "errors": errors,
                },
                ttl=300,
                tags=["supervisor", "decomposition"],
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
            segments.append(
                f"[{agent_label} — {r.get('original_question', '')[:50]}]\n{r['answer']}"
            )

        context = "\n\n---\n\n".join(segments)

        try:
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
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            # 回退：拼接结果
            lines = [f"以下是多个来源的信息汇总:\n"]
            for r in results:
                lines.append(f"--- {r['agent']} ---")
                lines.append(r.get("answer", "（无结果）"))
            return "\n".join(lines)
