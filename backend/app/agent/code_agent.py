"""CodeAgent — 代码辅助 Agent。

处理编程相关问题：代码编写、代码审查、性能分析、架构建议等。
使用 LLM 生成代码，并通过共享记忆了解项目上下文。

安全约束:
  - 不会直接执行用户提供的代码（仅生成和分析）
  - 所有生成的代码附带解释说明
  - 不会访问或修改系统关键文件

支持的动作:
  - "chat":       回答问题 + 生成代码
  - "review":     审查代码片段
  - "explain":    解释代码功能
"""

import asyncio
import logging
import time as tmod
from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.memory import MemoryManager
from app.config import settings
from app.monitor import record_model_call

logger = logging.getLogger(__name__)

# 代码生成的安全提示词
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

    处理编程相关的查询，使用 LLM 生成代码并提供解释。
    适合处理 Python、JavaScript、TypeScript 等常见语言的编程问题。
    """

    def __init__(
        self,
        memory: Optional[MemoryManager] = None,
        agent_id: str = "code",
    ):
        self._id = agent_id
        self._memory = memory
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base

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
                language = payload.get("language", "")
                conv_id = payload.get("conversation_id", "")

                # 获取相关记忆（按 conversation 隔离）
                memory_context = await self._build_memory_context(namespace=conv_id)

                answer = await self._ask_llm(
                    system_prompt=CODE_SYSTEM_PROMPT + memory_context,
                    user_message=question,
                )

                # 缓存到记忆（按 conversation 隔离）
                if self._memory:
                    await self._memory.set(
                        f"code_last_q",
                        question[:100],
                        ttl=120,
                        tags=["code"],
                        namespace=conv_id,  # 🔒 Session 隔离
                    )

                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="chat",
                    payload={"answer": answer, "sources": [], "steps": []},
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
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": str(e)},
                thread_id=msg.thread_id,
            )

    async def _build_memory_context(self, namespace: str = "") -> str:
        """从共享记忆中构建上下文提示（按 conversation 隔离）。"""
        if not self._memory:
            return ""

        try:
            code_memories = await self._memory.get_by_tag("code", namespace=namespace)
            if code_memories:
                return "\n\n[项目上下文]\n" + "\n".join(
                    f"- {k}: {str(v)[:200]}"
                    for k, v in code_memories.items()
                )
        except Exception:
            pass
        return ""

    async def _ask_llm(self, system_prompt: str, user_message: str) -> str:
        """调用 LLM 生成回答。"""
        start = tmod.time()
        response = await litellm.acompletion(
            model=self._model,
            api_key=self._api_key,
            api_base=self._api_base,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(self._model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        return response.choices[0].message.content.strip()
