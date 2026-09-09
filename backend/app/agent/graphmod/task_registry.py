"""[opencode task_id resume] 子 Agent 委派任务注册表（进程内）。

对齐 opencode「`task_id` 复用同一子会话」：
  - `record`：每次委派完成后记录 该 task 的对话线索（问题 + 最终答复），供续跑；
  - `get_history`：task_id 命中时返回上次运行的 history（子 Agent 以 history 前置，
    续跑同一探索/规划线索）；
  - 内存态、重启即失效（与 opencode 持久 session 的差异：DB 侧主会话本身持久，
    子 Agent 无独立 DB 会话，此处以进程内线索近似）。

同时承载 [background 异步委派] 的结果桥：
  - `_BACKGROUND_RESULTS[conversation_id]` 累积后台任务完成后的合成 assistant 文本，
    父 Agent 下一轮 LLM 调用时注入 messages 顶部（对齐 opencode injectBackgroundResult）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TaskRegistry:
    """子 Agent 委派任务注册表：resume 历史 + background 结果桥（线程/协程安全）。"""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._background: dict[str, list[str]] = {}
        self._lock = None  # 惰性注入 asyncio.Lock（无事件循环时不可创建）

    def _ensure_lock(self):  # pragma: no cover - 简单惰性创建
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        return self._lock

    async def record(self, task_id: str, subagent_type: str, question: str, answer: str,
                     conversation_id: str = "") -> None:
        """追加/更新该 task 的对话线索（问题 + 最终答复），供 resume 复用。

        仅保存对话级摘要（question + answer），不保存后台全部 tool 消息，
        控制进程内存。带 conversation_id 时在 db/调试角度留有归属。
        """
        lock = self._ensure_lock()
        async with lock:
            prev = self._tasks.get(task_id)
            history = (prev or {}).get("history", [])
            if question:
                history.append({"role": "user", "content": question})
            if answer:
                history.append({"role": "assistant", "content": answer})
            # 控制上限：只保留最近 8 轮（防单 task 无限膨胀）
            history = history[-16:]
            self._tasks[task_id] = {
                "subagent_type": subagent_type,
                "history": history,
                "conversation_id": conversation_id,
                "question": question,
                "answer": answer,
            }

    def get_history(self, task_id: str) -> list:
        """返回该 task 上次运行的对话线索（无则空列表 → 全新上下文）。"""
        info = self._tasks.get(task_id)
        return list((info or {}).get("history", []))

    def get(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)

    def peek_last_answer(self, task_id: str) -> str:
        """取该 task 最近一次答复（background 模式下发起方可能已走，供后续查询）。"""
        info = self._tasks.get(task_id)
        return (info or {}).get("answer") or ""

    def record_ids(self, subagent_type: str = "") -> list:
        return [tid for tid, v in self._tasks.items()
                if not subagent_type or v.get("subagent_type") == subagent_type]

    # ── background 结果桥 ──────────────────────────────────────────────

    def push_background_result(self, conversation_id: str, assistant_text: str) -> None:
        """记录某会话的后台任务完成结果（供父 Agent 下一轮吸收）。"""
        if not conversation_id:
            return
        bucket = self._background.setdefault(conversation_id, [])
        bucket.append(assistant_text)

    def drain_background_results(self, conversation_id: str) -> list:
        """取出（并清空）某会话所有已完成的后台结果。"""
        results = self._background.pop(conversation_id, None) or []
        return results


# 模块级单例：RAGAgent 实例与 generate 循环共享（tools.py 写 / generate.py 读）
_task_registry = TaskRegistry()


def get_task_registry() -> TaskRegistry:
    """获取全局委派任务注册表单例（进程内）。"""
    return _task_registry


def task_background_tag(task_id: str, conversation_id: str) -> str:
    """构造后台任务注入父会话的合成 assistant 文本头。"""
    return f"<task id=\"{task_id}\" state=\"completed\"><task_result>"