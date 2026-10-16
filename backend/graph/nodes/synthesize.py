"""结果综合节点（对齐 agent.md §3.2 / §7.3）。

把各子任务结果合成为最终输出。单子任务时直接采用；多子任务时可选 LLM 汇总。
"""

from __future__ import annotations

from typing import Any, Callable

from ..usage import usage_from_result


def make_synthesize_node(
    model: Any | None = None,
) -> Callable[[dict[str, Any]], Any]:
    async def synthesize_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("final_output"):
            return {}

        sub_results = state.get("sub_results") or {}
        if not sub_results:
            return {"final_output": state.get("draft_output") or "(无结果)"}

        if len(sub_results) == 1:
            return {"final_output": next(iter(sub_results.values()))}

        if model is not None:
            try:
                joined = "\n\n".join(
                    f"### 子任务 {k}\n{v}" for k, v in sub_results.items()
                )
                res = await model.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": "把以下子任务结果综合为连贯的最终回答。",
                        },
                        {"role": "user", "content": joined},
                    ]
                )
                if res.content:
                    return {
                        "final_output": res.content,
                        "token_usage": usage_from_result(res, getattr(model, "tier", None)),
                    }
            except Exception:  # noqa: BLE001
                pass

        return {
            "final_output": "\n\n".join(
                f"### 子任务 {k}\n{v}" for k, v in sub_results.items()
            )
        }

    return synthesize_node
