"""PlanAgent — 规划模式 Agent（对齐 opencode plan agent）。

纯 LLM 输出，不执行任何工具。分析用户需求，生成结构化实施计划。
适合需要先规划再执行的复杂任务。

支持的动作:
  - "chat":  分析需求并输出结构化计划
"""

import asyncio
import logging
import time as tmod
from pathlib import Path
from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.memory import MemoryManager
from app.agent.stream_events import agent_meta, emit, step_event
from app.config import settings
from app.monitor import record_model_call
from app.prompt_log import log_prompt

logger = logging.getLogger(__name__)


def _plan_path(conversation_id: str) -> Path:
    """计划文件路径：<data>/plans/<conversation_id>/plan.md（对齐 opencode data/plans/*.md）。

    conversation_id 中的非法路径字符会被替换，防止目录穿越。
    """
    from app.storage.paths import global_paths
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in (conversation_id or "")) or "session"
    plans_dir = global_paths()["data"] / "plans" / safe_id
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir / "plan.md"

PLAN_SYSTEM_PROMPT = """你是一个专业的项目规划助手。你的任务是分析用户需求，生成结构化实施计划。

核心原则:
- 不执行任何操作，只输出计划
- 计划必须具体、可执行
- 每步包含明确的文件/工具/预期结果
- 识别潜在风险和依赖关系

输出格式（Markdown）:

## 实施计划

### 步骤 1: [步骤标题]
- **目标**: [要达成的目标]
- **涉及文件**: [需要修改/创建的文件]
- **操作**: [具体操作描述]
- **预期结果**: [完成后的状态]

### 步骤 2: [步骤标题]
...

### 风险与依赖
- [潜在风险]
- [步骤间依赖关系]

### 预估工作量
- [总体评估]

要求:
- 使用中文输出
- 计划粒度适中（不要太粗也不要太细）
- 标注关键决策点
- 考虑代码质量和可维护性"""


class PlanAgent(BaseAgent):
    """规划模式 Agent。

    纯 LLM 输出，不执行任何工具。分析用户需求，生成结构化实施计划。
    """

    def __init__(
        self,
        memory: Optional[MemoryManager] = None,
        agent_id: str = "plan",
    ):
        self._id = agent_id
        self._memory = memory
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base
        if self._model.startswith("ollama/"):
            self._api_key = "ollama"
            self._api_base = None

    @property
    def agent_id(self) -> str:
        return self._id

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        try:
            if action == "chat":
                question = payload.get("question", "")
                conv_id = payload.get("conversation_id", "")
                event_queue = payload.get("_event_queue")
                history = payload.get("history") or []
                name, avatar = agent_meta(self._id)
                emit(event_queue, {
                    "type": "agent_start",
                    "agent_id": self._id,
                    "agent_name": name,
                    "agent_avatar": avatar,
                })

                # 纯 LLM 生成计划
                start = tmod.time()
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event("plan", "生成实施计划", "running"),
                })

                answer = await self._generate_plan(question, history)

                # [opencode 对齐] 计划落盘为会话级产物：<data>/plans/<conv>/plan.md
                # 供后续 build 阶段复读执行（对齐 opencode plan-mode 写计划文件 + build-switch）
                plan_path = _plan_path(conv_id)
                try:
                    await asyncio.to_thread(plan_path.write_text, answer or "", encoding="utf-8")
                    if answer:
                        answer = answer + f"\n\n> 计划文件已保存: {plan_path}（后续可直接引用该文件执行）"
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to persist plan file: %s", e)
                    plan_path = None

                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event(
                        "plan", "生成实施计划", "completed",
                        duration_ms=(tmod.time() - start) * 1000,
                    ),
                })

                # 缓存到记忆
                if self._memory:
                    try:
                        await self._memory.set(
                            "plan_last_q",
                            question[:100],
                            ttl=120,
                            tags=["plan"],
                            namespace=conv_id,
                        )
                    except Exception:
                        pass

                emit(event_queue, {
                    "type": "agent_done",
                    "agent_id": self._id,
                    "content": answer,
                })
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="chat",
                    payload={
                        "answer": answer, "sources": [], "steps": [],
                        "plan_path": str(plan_path) if plan_path else "",
                    },
                    thread_id=msg.thread_id,
                )

            else:
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="error", action=action,
                    payload={"error": f"Unknown action: {action}"},
                    thread_id=msg.thread_id,
                )

        except Exception as e:
            logger.exception("PlanAgent error on action=%s", action)
            emit(payload.get("_event_queue"), {
                "type": "agent_error",
                "agent_id": self._id,
                "error": str(e),
            })
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": str(e)},
                thread_id=msg.thread_id,
            )

    async def _generate_plan(self, question: str, history: list[dict]) -> str:
        """调用 LLM 生成结构化计划。"""
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        ]
        # 添加历史对话（最多 4 轮；规划任务通常无需很多上文，且 tool_task 委派时 history 为空）
        for h in history[-8:]:
            if not isinstance(h, dict):
                continue
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": question})

        log_prompt("plan_agent.generate_plan", messages, model=self._model)

        start = tmod.time()
        response = await litellm.acompletion(
            model=self._model,
            api_key=self._api_key,
            api_base=self._api_base,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
            cache_prompt=True,
        )
        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(self._model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)

        msg = response.choices[0].message
        # [真实数据回归] deepseek-v4-flash 等 reasoning 模型偶发把正史写进
        # reasoning_content、content 为空（finish_reason=length）；做回退拼接。
        text = (msg.content or "").strip() or (
            getattr(msg, "reasoning_content", None) or ""
        ).strip()
        return text
