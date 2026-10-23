"""Tool Adapter：Agent Loop 与 LiteLLM 之间唯一的适配层（agent-4 §3）。

Loop 只看到统一的中立结构（``ChatResult`` / ``ToolCall`` / 事件流），
不感知原生 tool_calls 或文本定界符的差异。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from models.types import ChatResult, ToolCall

from .adapter import apply_result, inject_fallback_prompt
from .capabilities import FALLBACK, NATIVE, resolve_mode
from .delimiters import HarnessToolCall
from .history import normalize_history
from .streaming import PreludeSuppressor, StreamToolCallParser

logger = logging.getLogger(__name__)

_FINISHED = object()


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallReady:
    """回退路径下定界符闭合即产出；Loop 可据此立即执行（不必等整段响应）。"""

    call: ToolCall


@dataclass(frozen=True)
class Done:
    response: ChatResult


HarnessEvent = TextDelta | ToolCallReady | Done


def _infer_model_id(model: Any) -> str:
    for attr in ("model_name", "model_id", "name"):
        value = getattr(model, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:  # noqa: BLE001
                continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _to_tool_call(call: HarnessToolCall) -> ToolCall:
    return ToolCall(
        id=f"harness-{id(call):x}",
        name=call.name,
        arguments=call.arguments,
    )


class ToolAdapter:
    """能力探测 + 两条路径 + 统一输出。"""

    def __init__(
        self,
        model: Any,
        *,
        mode: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.model = model
        self.model_id = model_id or _infer_model_id(model)
        self.mode = mode or resolve_mode(self.model_id)

    def prepare(
        self, messages: list[Any], tools: list[dict[str, Any]] | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        from models.router import _render_message

        rendered = normalize_history(
            [_render_message(m) for m in messages], self.mode
        )
        if tools and self.mode == FALLBACK:
            return inject_fallback_prompt(rendered, tools), None
        return rendered, tools

    async def _raw_ainvoke(
        self, messages: list[Any], tools: list[dict[str, Any]] | None
    ) -> ChatResult:
        raw = getattr(self.model, "raw_ainvoke", None)
        if callable(raw):
            return await raw(messages, tools=tools)
        return await self.model.ainvoke(messages, tools=tools)

    async def _raw_stream(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
    ) -> Any:
        raw = getattr(self.model, "raw_stream", None)
        if callable(raw):
            return await raw(messages, tools=tools, on_text=on_text)
        return await self.model.astream(messages, tools=tools, on_text=on_text)

    async def complete(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        prepared, call_tools = self.prepare(messages, tools)
        result = await self._raw_ainvoke(prepared, call_tools)
        return apply_result(result, self.mode)

    async def stream(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        """流式事件：文本增量 / 工具调用就绪 / 完成。

        回退路径下定界符一闭合就 yield ``ToolCallReady``；原生路径抑制
        “解释性前置文本 + 内嵌 JSON 调用”里的前置文本。
        """
        prepared, call_tools = self.prepare(messages, tools)
        parser = StreamToolCallParser() if self.mode == FALLBACK else None
        suppressor = (
            PreludeSuppressor()
            if self.mode == NATIVE and bool(tools)
            else None
        )
        queue: asyncio.Queue[Any] = asyncio.Queue()
        texts: list[str] = []
        calls: list[ToolCall] = []

        def on_text(chunk: str) -> None:
            if not chunk:
                return
            if parser is not None:
                for event in parser.feed(chunk):
                    if event["type"] == "text":
                        texts.append(event["text"])
                        queue.put_nowait(TextDelta(event["text"]))
                    else:
                        call = _to_tool_call(event["call"])
                        calls.append(call)
                        queue.put_nowait(ToolCallReady(call))
                return
            if suppressor is not None:
                kept = suppressor.feed(chunk)
                if kept:
                    texts.append(kept)
                    queue.put_nowait(TextDelta(kept))
                return
            texts.append(chunk)
            queue.put_nowait(TextDelta(chunk))

        async def produce() -> Any:
            try:
                return await self._raw_stream(prepared, call_tools, on_text)
            finally:
                queue.put_nowait(_FINISHED)

        task = asyncio.create_task(produce())
        while True:
            event = await queue.get()
            if event is _FINISHED:
                break
            yield event

        # 流结束时释放解析器剩余内容
        if parser is not None:
            tail = parser.finalize()
        elif suppressor is not None:
            tail = [{"type": "text", "text": suppressor.finalize()}]
        else:
            tail = []
        for event in tail:
            if event["type"] == "text" and event.get("text"):
                texts.append(event["text"])
                yield TextDelta(event["text"])
            elif event["type"] == "tool_call":
                call = _to_tool_call(event["call"])
                calls.append(call)
                yield ToolCallReady(call)

        result = await task
        result = apply_result(result, self.mode)
        if parser is not None:
            # 解析器已增量产出调用；避免用整段解析结果重复一次
            result.tool_calls = calls or result.tool_calls
            if texts:
                result.content = "".join(texts)
        yield Done(result)


def get_tool_adapter(
    model: Any, *, mode: str | None = None, model_id: str | None = None
) -> ToolAdapter:
    return ToolAdapter(model, mode=mode, model_id=model_id)


__all__ = [
    "Done",
    "HarnessEvent",
    "TextDelta",
    "ToolAdapter",
    "ToolCallReady",
    "get_tool_adapter",
]
