"""L1 单元测试：压缩摘要 + 精确值回填。

v2 起移除 LLM 门禁：只要有模型就生成摘要，失败才走确定性兜底。
摘要生成后纯规则回填精确值（确定性保证）。
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from cognition.compressor import (
    enforce_precision,
    extract_precision_values,
)
from graph.nodes.compress import make_compress_node


def _big(text: str, n: int) -> str:
    return (text + " ") * n


# ── 精确值提取 / 回填 ──


def test_extract_precision_values():
    text = "见 /etc/app/config.yaml 与 C:\\tool\\a.txt，端口 8080，命令 `npm run build`"
    vals = extract_precision_values(text)
    assert any("/etc/app/config.yaml" in v for v in vals)
    assert "8080" in vals
    assert "`npm run build`" in vals


def test_enforce_precision_backfills_missing():
    older = [HumanMessage(content="部署脚本在 /opt/deploy.sh，端口 9090")]
    summary = "已讨论部署"  # 摘要漏了精确值
    out = enforce_precision(summary, older)
    assert "精确值补充" in out
    assert "/opt/deploy.sh" in out
    assert "9090" in out


def test_enforce_precision_noop_when_present():
    older = [HumanMessage(content="端口 9090")]
    summary = "端口 9090 已确认"
    assert enforce_precision(summary, older) == summary


def test_enforce_precision_ignores_json_escaped_windows_path():
    path = r"E:\AgentSuper\backend\harness\compression.py"
    older = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": path}),
                    },
                }
            ],
        }
    ]
    summary = f"重点文件 {path}，尚未修改。"

    assert enforce_precision(summary, older) == summary
    assert path in extract_precision_values(
        json.dumps({"path": path})
    )


# ── 节点行为 ──


class _CaptureModel:
    def __init__(self, content="要点摘要"):
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.content = self.content
        return r


async def test_node_calls_model_when_gate_passes():
    model = _CaptureModel("摘要：端口 8080")
    node = make_compress_node(model, keep_recent=2)
    older = [HumanMessage(content=_big("端口 8080 路径 /a/b.txt version 3", 400)) for _ in range(3)]
    recent = [HumanMessage(content="最近1"), HumanMessage(content="最近2")]
    out = await node({"messages": older + recent, "compress_count": 0})
    assert model.calls >= 1
    assert out["compress_count"] == 1
    assert out["context_compressed"] is True


async def test_node_calls_model_even_for_short_content():
    model = _CaptureModel()
    node = make_compress_node(model, keep_recent=2)
    msgs = [HumanMessage(content="短") for _ in range(4)]
    out = await node({"messages": msgs, "compress_count": 0})
    assert model.calls == 1
    assert out.get("compress_count", 0) == 1


async def test_node_skips_model_when_disabled():
    # model=None → 规则截断
    node = make_compress_node(None, keep_recent=2)
    older = [HumanMessage(content=_big("端口 8080 路径 /a/b.txt", 400))]
    recent = [HumanMessage(content="r1"), HumanMessage(content="r2")]
    out = await node({"messages": older + recent, "compress_count": 0})
    assert out["context_compressed"] is True
    assert out.get("compress_count", 0) == 0


async def test_node_backfills_precision_even_when_llm_omits_it():
    """精确值回填是「每次压缩后」的确定性保证：LLM 摘要漏掉的路径/数字必须补回。"""
    model = _CaptureModel("已压缩，但没写具体值")
    node = make_compress_node(model, keep_recent=2)
    older = [HumanMessage(content="端口 8080 路径 /a/b.txt version 3")]
    recent = [HumanMessage(content="最近1"), HumanMessage(content="最近2")]
    out = await node({"messages": older + recent, "compress_count": 0})
    assert model.calls == 1
    summary = out["context_summary"]
    assert "8080" in summary and "/a/b.txt" in summary

