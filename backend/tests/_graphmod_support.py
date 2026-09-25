# -*- coding: utf-8 -*-
"""graphmod 测试支持模块（不匹配 pytest 收集规则，仅被测试文件 import）。"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.agent.graphmod.core import RAGAgent  # noqa: E402


def build_agent(
    retriever=None,
    reranker=None,
    skill_loader=None,
    plugin_loader=None,
    custom_tools=None,
    memory=None,
):
    r = retriever or SimpleNamespace(is_empty=True, invoke=lambda q, k=3: [])
    return RAGAgent(
        r,
        skill_loader=skill_loader,
        plugin_loader=plugin_loader,
        reranker=reranker,
        custom_tools=custom_tools,
        memory=memory,
    )


def make_state(**over):
    st = {
        "messages": [],
        "question": "q",
        "context": [],
        "answer": "",
        "sources": [],
        "model": None,
        "history": [],
        "use_vector_db": False,
        "files": [],
        "steps": [],
        "tokens": {},
        "finish": "stop",
        "_event_queue": None,
        "_on_activity": None,
        "_task": None,
        "_cwd": "",
        "_task_depth": 0,
        "conversation_id": "",
    }
    st.update(over)
    return st


def fake_task():
    return SimpleNamespace(
        record_compaction=lambda: None,
        increment_step=lambda: None,
        increment_tool_calls=lambda n: None,
        mark_completed=lambda: None,
        mark_failed=lambda e: None,
        to_dict=lambda: {"conversation_id": "c"},
        save=lambda: None,
    )


class FakeLLM:
    """可编程 _llm_call 替身：记录调用参数、按脚本顺序吐响应。"""

    def __init__(self):
        self.calls = []  # (model, messages, tool_defs)
        self.responses = []

    def response(self, content="", tool_calls=None, finish_reason="stop", usage=None):
        tc = [
            SimpleNamespace(
                id=f"call_{i}",
                type="function",
                function=SimpleNamespace(name=n, arguments=a),
            )
            for i, (n, a) in enumerate(tool_calls or [])
        ]
        msg = SimpleNamespace(content=content, tool_calls=tc or None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)],
            usage=usage
            or SimpleNamespace(
                prompt_tokens=5,
                completion_tokens=3,
                prompt_cache_hit_tokens=2,
                prompt_cache_miss_tokens=3,
            ),
        )

    async def __call__(self, model, messages, tool_defs, state=None):
        self.calls.append((model, messages, tool_defs))
        if not self.responses:
            raise AssertionError("no programmed response left for _llm_call")
        return self.responses.pop(0)
