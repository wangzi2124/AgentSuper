"""CodeAgent — 代码辅助 Agent（对齐 opencode "build agent 即代码 Agent" 设计）。

背景：原先 code 子 Agent 只用 4 轮、12K 上下文的简化工具循环，能力远弱于主 Agent
（丢失文件工具全量 schema、技能、插件、生成器、对话历史、会话工作目录），被路由到
code 反而"降级"。现在 code 直接委派给共享的主 RAGAgent 执行——它本身就带完整文件
工具 + 技能 + 插件 + 生成器 + 工作区权限，等价于 opencode 的 primary build agent。
子 Agent 面板事件（agent_start / agent_step / agent_done）仍由本包装器发出，
前端 multi-agent UI 无需改动。

支持的动作:
  - "chat":     完整代码/文件任务（委派主 Agent，默认关闭向量库，history/directory 透传）
  - "review":   审查代码片段
  - "explain":  解释代码功能
"""

import logging
import time as tmod
from typing import AsyncIterator, Callable, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.graph import RAGAgent
from app.agent.memory import MemoryManager
from app.agent.stream_events import TaggedEventQueue, agent_meta, emit, step_event
from app.config import settings
from app.monitor import record_model_call
from app.prompt_log import log_prompt  # [prompt log v1]

logger = logging.getLogger(__name__)

# 代码审查/解释的轻量系统提示词（仅 review/explain 动作使用，chat 已委派主 Agent）
CODE_SYSTEM_PROMPT = """你是一个专业的代码助手。你可以:

1. **编写代码**: 根据需求编写干净的、有注释的代码
2. **审查代码**: 指出代码中的问题、安全隐患和优化机会
3. **解释代码**: 清晰解释代码的功能、逻辑和设计模式

安全规则:
- 永远不要生成恶意代码、病毒或攻击脚本
- 永远不要建议不安全的编码实践
- 对涉及密码、API Key 等敏感信息的代码给出安全警告
- 如果问题涉及安全漏洞，优先建议修复方案而非利用方法

回答要求:
- 使用中文回答，代码中的注释也使用中文
- 代码块标注语言类型（如 ```python）
- 对复杂的代码逻辑给出解释
- 如果有多种实现方式，比较优劣后推荐最佳方案"""


class CodeAgent(BaseAgent):
    """代码辅助 Agent。

    chat 动作委派给共享主 RAGAgent（完整工具链 + 工作区权限），
    review/explain 动作仍走轻量 LLM 调用。
    """

    def __init__(
        self,
        inner: RAGAgent,
        memory: Optional[MemoryManager] = None,
        agent_id: str = "code",
        heartbeat: Optional[Callable[[str, str], None]] = None,
    ):
        self._inner = inner
        self._id = agent_id
        self._memory = memory
        self._heartbeat = heartbeat
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base

    @property
    def agent_id(self) -> str:
        return self._id

    def _notify(self, progress: str) -> None:
        """把处理进度转发给总线心跳（用于超时宽限续期 + 已完成步骤回传）。"""
        if self._heartbeat:
            try:
                self._heartbeat(self._id, progress)
            except Exception:
                pass

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
                directory = payload.get("directory", "")
                task_depth = int(payload.get("_task_depth", 0) or 0)
                name, avatar = agent_meta(self._id)
                emit(event_queue, {
                    "type": "agent_start",
                    "agent_id": self._id,
                    "agent_name": name,
                    "agent_avatar": avatar,
                })

                # 委派主 Agent：完整文件工具 + 技能 + 插件 + 工作区权限（对齐 opencode build agent）
                tagged = TaggedEventQueue(event_queue, self._id) if event_queue is not None else None
                start = tmod.time()
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event("generate", "生成回答", "running"),
                })
                if settings.long_task_step_mode:
                    # [C5 · 方案 E/F] 长任务小步快走：多步骤任务拆计划、每步独立
                    # fresh-context 请求执行，步间只传落盘 STEP_STATE（上下文不膨胀）。
                    from app.agent.long_task import LongTaskCoordinator
                    result = await LongTaskCoordinator(
                        self._inner, max_steps=settings.long_task_max_steps,
                    ).run(question, directory=directory, conversation_id=conv_id)
                else:
                    result = await self._inner.invoke(
                        question=question,
                        model=payload.get("model"),
                        history=history,
                        use_vector_db=payload.get("use_vector_db", False),
                        files=payload.get("files", []),
                        conversation_id=conv_id,
                        on_activity=self._notify,
                        event_queue=tagged,
                        directory=directory,
                        task_depth=task_depth,
                    )
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event(
                        "generate", "生成回答", "completed",
                        duration_ms=(tmod.time() - start) * 1000,
                    ),
                })

                answer = result.get("answer", "")
                # 缓存到记忆（按 conversation 隔离）
                if self._memory:
                    try:
                        await self._memory.set(
                            f"code_last_q",
                            question[:100],
                            ttl=120,
                            tags=["code"],
                            namespace=conv_id,  # 🔒 Session 隔离
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
                        "answer": answer,
                        "sources": result.get("sources", []),
                        "steps": result.get("steps", []),
                        # 透传主 Agent 真实 LLM 用量，供 supervisor 汇总落库
                        "tokens": result.get("tokens") or {},
                    },
                    thread_id=msg.thread_id,
                )

            elif action == "review":
                code = payload.get("code", "")
                language = payload.get("language", "python")
                review_prompt = f"""请审查以下 {language} 代码，指出：

1. 功能正确性
2. 潜在 bug
3. 安全隐患
4. 性能问题
5. 代码风格改进

代码:
```{language}
{code}
```"""
                answer = await self._ask_llm(
                    system_prompt=CODE_SYSTEM_PROMPT,
                    user_message=review_prompt,
                )
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="review",
                    payload={"answer": answer, "sources": [], "steps": []},
                    thread_id=msg.thread_id,
                )

            elif action == "explain":
                code = payload.get("code", "")
                language = payload.get("language", "python")
                explain_prompt = f"""请解释以下 {language} 代码的功能和工作原理：

```{language}
{code}
```"""
                answer = await self._ask_llm(
                    system_prompt=CODE_SYSTEM_PROMPT,
                    user_message=explain_prompt,
                )
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="explain",
                    payload={"answer": answer, "sources": [], "steps": []},
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
            logger.exception("CodeAgent error on action=%s", action)
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

    async def _ask_llm(self, system_prompt: str, user_message: str) -> str:
        """调用 LLM 生成回答。"""
        start = tmod.time()
        _msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        log_prompt("code_agent.ask_llm", _msgs, model=self._model)  # [prompt log v1]
        response = await litellm.acompletion(
            model=self._model,
            api_key=self._api_key,
            api_base=self._api_base,
            messages=_msgs,
            max_tokens=2048,
            temperature=0.3,
        )
        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(self._model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        return response.choices[0].message.content.strip()
