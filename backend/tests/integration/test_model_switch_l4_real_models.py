r"""L4：真实模型矩阵回归（agent-3 §3.4 黄金集）。

默认跳过（CI 不烧真实 token）。启用方式：

```powershell
$env:L4_MODEL_SWITCH = "1"
$env:L4_MODEL_A = "deepseek/deepseek-v4-flash"
$env:L4_MODEL_B = "deepseek/deepseek-v4.1-flash"
# 可选：多组模型对（分号/逗号分隔，格式 a:b）
$env:L4_MODEL_PAIRS = "deepseek/deepseek-v4-flash:deepseek/deepseek-v4.1-flash;ollama/qwen3.5:4b:ollama/llama3.1:latest"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_model_switch_l4_real_models.py -q
```

校验口径（与 §3.4 一致）：模型 A 记录精确事实 → 切到模型 B（强制压缩迁移）→
模型 B 必须答出事实（代号/端口/路径），否则视为上下文迁移丢失。
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import app.deps as deps
from app.deps import ModelSwitchError, PlatformContext
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph
from models.router import get_model

_L4_ENABLED = os.environ.get("L4_MODEL_SWITCH", "").lower() in {"1", "true", "yes"}
MODEL_A = os.environ.get("L4_MODEL_A", "").strip()
MODEL_B = os.environ.get("L4_MODEL_B", "").strip()


def _model_pairs() -> list[tuple[str, str]]:
    raw = os.environ.get("L4_MODEL_PAIRS", "").strip()
    pairs: list[tuple[str, str]] = []
    if raw:
        for item in raw.replace(",", ";").split(";"):
            item = item.strip()
            if not item or ":" not in item:
                continue
            head, tail = item.split(":", 1)
            if head.strip() and tail.strip():
                pairs.append((head.strip(), tail.strip()))
    if not pairs and MODEL_A and MODEL_B:
        pairs.append((MODEL_A, MODEL_B))
    return pairs or [("model-a", "model-b")]


_PAIRS = _model_pairs()

pytestmark = pytest.mark.skipif(
    not (_L4_ENABLED and _PAIRS and _PAIRS[0][0] != "model-a"),
    reason=(
        "L4 真实模型回归默认跳过：需 L4_MODEL_SWITCH=1 + L4_MODEL_A/L4_MODEL_B"
        "（或 L4_MODEL_PAIRS）+ 真实 LLM 凭证"
    ),
)


class _Sessions:
    def __init__(self, sess):
        self.sess = sess
        self.persisted: list[str] = []

    def get(self, session_id):
        return self.sess

    def persist(self, session_id):
        self.persisted.append(session_id)

    def touch(self, session_id):
        self.persisted.append(session_id)


_FACT_CODE = "FALCON-7788"
_FACT_PORT = "8080"
_FACT_PATH = "D:/l4/project/app.py"


def _long_context() -> str:
    filler = "系统需要保持稳定的上下文字段与历史信息。" * 150
    return (
        f"请原样记住以下项目事实：项目代号 {_FACT_CODE}，监听端口 {_FACT_PORT}，"
        f"入口文件 {_FACT_PATH}。\n背景资料（无需回答）：{filler}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_a,model_b",
    _PAIRS,
    ids=[f"{a}->{b}" for a, b in _PAIRS],
)
async def test_real_model_switch_preserves_facts(
    monkeypatch, tmp_path, model_a, model_b
):
    """真实模型 A→B：迁移后 B 仍能答出精确事实（路径/代号/端口）。"""
    if model_a == model_b:
        pytest.skip("模型对相同，切换是 no-op，无法验证迁移")

    from models.catalog import read_limits

    if int((read_limits(model_b) or {}).get("context_length") or 0) <= 0:
        pytest.skip(f"模型目录缺少 {model_b} 的 context_length，无法通过切换门禁")

    # 强制小窗口：让 switch_model 必须走「摘要 + 近期原文」迁移（而不是直接沿用全量历史）。
    monkeypatch.setattr(
        "models.catalog.read_limits", lambda _mid: {"context_length": 3_000}
    )
    # 隔离切换备份/审计到 tmp。
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))

    # epoch 存储用内存替身（真实仓库依赖全局 SessionStore 初始化）。
    epochs: dict[str, tuple] = {}
    monkeypatch.setattr("app.session.repository.get_epoch", lambda sid: epochs.get(sid))

    def _upsert(sid, baseline, baseline_seq, snapshot):
        epochs[sid] = SimpleNamespace(
            baseline=baseline,
            baseline_seq=baseline_seq,
            snapshot=dict(snapshot),
        )

    monkeypatch.setattr("app.session.repository.upsert_epoch", _upsert)

    graph = build_main_graph(
        model=get_model("auto", max_tokens=1024),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_task_tool=False,
    )
    sess = SimpleNamespace(
        model=model_a,
        status="idle",
        directory=str(tmp_path),
        updated_at=0.0,
    )
    ctx = PlatformContext(
        graph=graph,
        registry=SimpleNamespace(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        sessions=_Sessions(sess),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
        skill_loader=None,
    )

    sid = f"ses_l4_{abs(hash((model_a, model_b))) % 10_000_000}"

    out = await ctx.run(_long_context(), session_id=sid, message_id="um_l4_1")
    assert out.get("final_output"), "模型 A 未产出回答"
    await graph.aupdate_state(
        {"configurable": {"thread_id": sid}},
        {"messages": [AIMessage(content=out["final_output"], id="am_l4_1")]},
    )

    try:
        switched = await ctx.switch_model(sid, model_b)
    except ModelSwitchError as exc:
        pytest.fail(f"切换失败（{model_a} -> {model_b}）：{exc}")

    assert switched["switched"] is True
    assert switched["summarized"] is True, "上下文未触发迁移摘要，L4 用例无效"
    binding = ctx.context_binding(sid)
    assert _FACT_PORT in binding["summary"]
    assert "app.py" in binding["summary"]

    answer = await ctx.run(
        "根据此前对话，项目代号、监听端口、入口文件路径分别是什么？只输出这三项。",
        session_id=sid,
        message_id="um_l4_2",
    )
    text = str(answer.get("final_output") or "")
    missing = [v for v in (_FACT_CODE, _FACT_PORT, "app.py") if v not in text]
    assert not missing, (
        f"模型 {model_b} 切换后未召回事实 {missing}；回答：{text[:400]}；"
        f"摘要：{binding['summary'][:400]}"
    )

    audit = tmp_path / "backend" / "data" / "model_switches" / "switches.jsonl"
    assert audit.exists()
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["status"] == "committed"
    assert events[-1]["from"] == model_a
    assert events[-1]["to"] == model_b
