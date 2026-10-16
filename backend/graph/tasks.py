"""结构化任务对象（对齐 agent.md §3.4 / §5.2）。

任务在 Agent 间以结构化对象传递（而非自然语言），包含目标 / 执行者 /
上下文 / 交付物 / 状态，便于并行扇出与结果合并。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

KNOWN_AGENTS: tuple[str, ...] = ("research", "code", "file")
KNOWN_SKILLS: tuple[str, ...] = ("rag", "web_search", "memory")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass
class TaskSpec:
    """单个结构化子任务。"""

    id: str
    objective: str
    agent: str = ""
    context: str = ""
    deliverable: str = ""
    skills: list[str] = field(default_factory=list)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        return cls(
            id=str(data.get("id") or ""),
            objective=str(data.get("objective") or data.get("desc") or ""),
            agent=str(data.get("agent") or ""),
            context=str(data.get("context") or ""),
            deliverable=str(data.get("deliverable") or ""),
            skills=[s for s in (data.get("skills") or []) if s in KNOWN_SKILLS],
            status=str(data.get("status") or "pending"),
        )


def _extract_json_array(text: str) -> str | None:
    fenced = _JSON_FENCE.search(text or "")
    candidate = fenced.group(1) if fenced else (text or "")
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    return candidate[start : end + 1]


def parse_task_specs(text: str, *, default_agent: str = "") -> list[TaskSpec]:
    """从 LLM 输出中解析任务列表（容错：非 JSON 返回空列表）。"""
    raw = _extract_json_array(text)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    specs: list[TaskSpec] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        spec = TaskSpec.from_dict(item)
        if not spec.id:
            spec.id = f"t{i + 1}"
        if spec.agent not in KNOWN_AGENTS:
            spec.agent = default_agent
        specs.append(spec)
    return specs
