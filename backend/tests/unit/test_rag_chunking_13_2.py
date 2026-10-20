"""L1 单元测试：§13.2 结构感知分块 + 父子文档 metadata（agent-2.md §13.2）。"""

import os

from cognition.document_processor import DocumentProcessor

REQUIRED_META = ("parent_id", "source_file", "section_title", "department")


def _write(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _assert_required_meta(chunks):
    for text, meta in chunks:
        for key in REQUIRED_META:
            assert key in meta, f"chunk missing {key}: {text[:30]!r}"
        assert meta["source_file"]
        assert meta["section_title"], f"empty section_title: {text[:30]!r}"
        assert meta["department"]


def test_markdown_parent_child_by_heading(tmp_path):
    md = (
        "# 标题\n\n引言。\n\n"
        "## 退货政策\n\n买方可在签收后 30 天内申请退货。\n\n"
        "### 退货流程\n\n1. 提交申请\n2. 退款\n\n"
        "## 配送\n\n7 个工作日送达。\n"
    )
    proc = DocumentProcessor()
    chunks, chapter_metas = proc.process(_write(tmp_path, "a.md", md), "d1", "a.md")
    _assert_required_meta(chunks)

    parents = [(t, m) for t, m in chunks if m.get("is_parent")]
    children = [(t, m) for t, m in chunks if not m.get("is_parent")]
    # H2 小节各产一个父块（标题/退货政策/配送）→ 3 个
    assert len(parents) == 3
    # H3 小节归属于最近 H2 → 同一 parent_id
    by_sec = {m["section_title"]: m["parent_id"] for _, m in chunks if not m.get("is_parent")}
    assert by_sec["退货政策"] == by_sec["退货流程"]
    # 子块带 section_title（拼入检索文本的依据）
    assert any(m["section_title"] == "配送" for _, m in children)


def test_markdown_child_size_bounded(tmp_path):
    body = "句子。" * 400  # 2000 字
    md = f"## 长小节\n\n{body}\n"
    proc = DocumentProcessor()
    chunks, _ = proc.process(_write(tmp_path, "b.md", md), "d2", "b.md")
    children = [t for t, m in chunks if not m.get("is_parent")]
    assert children
    # 子块目标 300~400 字符（允许标点边界的轻微上浮）
    for c in children:
        assert len(c) <= 450, f"child too large: {len(c)}"


def test_faq_pairs_not_split(tmp_path):
    faq = (
        "Q: 退货期限是多久？\nA: 签收后 30 天内。\n\n"
        "Q: 如何退款？\nA: 原路退回，7 个工作日。\n"
    )
    proc = DocumentProcessor()
    chunks, _ = proc.process(_write(tmp_path, "f.txt", faq), "d3", "f.txt")
    _assert_required_meta(chunks)
    blobs = [t for t, m in chunks if m.get("is_faq")]
    assert len(blobs) == 2
    # 每块同时含问题与答案
    assert all("Q:" in b and "A:" in b for b in blobs)


def test_table_becomes_standalone_chunk(tmp_path):
    md = (
        "## 配送时效\n\n见下表：\n\n"
        "| 地区 | 时效 |\n| --- | --- |\n| 华东 | 1 天 |\n| 西北 | 5 天 |\n"
    )
    proc = DocumentProcessor()
    chunks, _ = proc.process(_write(tmp_path, "t.md", md), "d4", "t.md")
    _assert_required_meta(chunks)
    tables = [t for t, m in chunks if m.get("is_table")]
    assert len(tables) == 1
    assert "华东" in tables[0] and "|" in tables[0]


def test_generic_chapter_paragraph_window(tmp_path):
    txt = (
        "第一章 概述\n\n本文介绍系统架构。模型层负责推理。\n\n"
        "第二章 部署\n\n支持容器化部署，推荐 Kubernetes。\n"
    )
    proc = DocumentProcessor()
    chunks, chapter_metas = proc.process(_write(tmp_path, "p.txt", txt), "d5", "p.txt")
    _assert_required_meta(chunks)
    assert any(m.get("is_parent") and m["section_title"] == "第一章" for _, m in chunks)
    # 父 + 子覆盖两章
    assert len(chapter_metas) == 2


def test_department_override(tmp_path):
    md = "## 小节\n\n正文。\n"
    proc = DocumentProcessor(department="sales")
    chunks, _ = proc.process(_write(tmp_path, "x.md", md), "d6", "x.md")
    assert all(m["department"] == "sales" for _, m in chunks)
