"""L1 单元测试：压缩 LLM 摘要门禁 + 精确值回填（agent-1.md §1.4 / §1.5）。

原则：压缩=低成本高收益可积极启用，但每次调模型都要过门禁：
C1 被压缩内容 ≥5000 token；C2 含精确值关键词；C3 本会话 LLM 摘要 <3 次。
越不过门禁 → 规则截断（不调模型）。摘要生成后纯规则回填精确值（确定性保证）。
"""

from langchain_core.messages import HumanMessage

from cognition.compressor import (
    PRECISION_PATTERNS,
    contains_precision_keyword,
    enforce_precision,
    extract_precision_values,
    should_use_llm_summary,
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


def test_contains_precision_keyword():
    assert contains_precision_keyword("the version is 3")
    assert contains_precision_keyword("端口 8080")
    assert contains_precision_keyword("/etc/x.conf")  # 精确值模式命中
    assert not contains_precision_keyword("今天天气不错")


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


# ── 门禁 ──


def test_gate_blocks_small_content():
    # C1 失败：内容太少（<5000 token）
    older = [HumanMessage(content="端口 8080")]
    assert should_use_llm_summary(older, min_tokens=5000) is False


def test_gate_blocks_when_no_precision():
    # C2 失败：内容够大但无任何精确值/关键词
    older = [HumanMessage(content=_big("闲聊内容没有关键信息", 500))]
    assert should_use_llm_summary(older, min_tokens=5000) is False


def test_gate_blocks_after_max_calls():
    # C3 失败：本会话已用满 LLM 摘要次数
    older = [HumanMessage(content=_big("端口 8080 version id: 3 路径 /a/b.txt", 500))]
    assert should_use_llm_summary(older, compress_count=0, min_tokens=5000) is True
    assert should_use_llm_summary(older, compress_count=3, min_tokens=5000) is False


def test_gate_passes_when_all_true():
    older = [HumanMessage(content=_big("端口 8080 命令 `ls` 路径 /a/b.txt", 400))]
    assert should_use_llm_summary(older, compress_count=0, min_tokens=5000) is True


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
    assert model.calls == 1
    assert out["compress_count"] == 1
    assert out["context_compressed"] is True


async def test_node_skips_model_when_gate_fails():
    model = _CaptureModel()
    node = make_compress_node(model, keep_recent=2)
    # 内容太少 → C1 失败 → 规则截断，不调模型
    msgs = [HumanMessage(content="短") for _ in range(4)]
    out = await node({"messages": msgs, "compress_count": 0})
    assert model.calls == 0
    assert out.get("compress_count", 0) == 0


async def test_node_skips_model_when_disabled():
    # model=None → 规则截断
    node = make_compress_node(None, keep_recent=2)
    older = [HumanMessage(content=_big("端口 8080 路径 /a/b.txt", 400))]
    recent = [HumanMessage(content="r1"), HumanMessage(content="r2")]
    out = await node({"messages": older + recent, "compress_count": 0})
    assert out["context_compressed"] is True
    assert out.get("compress_count", 0) == 0


async def test_node_backfills_precision_on_rule_path():
    """§1.5：精确值回填是「每次压缩后」的确定性保证 —— 门禁失败走规则截断时
    精确值（路径/命令/数字）同样会随 older 被丢弃，必须回填，不能只补 LLM 分支。"""
    model = _CaptureModel()  # C1 失败 → 不调模型，走规则截断
    node = make_compress_node(model, keep_recent=2)
    older = [HumanMessage(content="端口 8080 路径 /a/b.txt version 3")]
    recent = [HumanMessage(content="最近1"), HumanMessage(content="最近2")]
    out = await node({"messages": older + recent, "compress_count": 0})
    assert model.calls == 0  # 未调模型（规则路径）
    summary = out["context_summary"]
    assert "精确值补充" in summary
    assert "8080" in summary and "/a/b.txt" in summary

