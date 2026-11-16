"""L1 单元测试：§13.2 结构感知分块 + 父子文档 metadata"""

import os

from cognition.document_processor import DocumentProcessor

REQUIRED_META = ("parent_id", "source_file", "section_title", "department")


def _write(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
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
    assert len(parents) == 3
    by_sec = {m["section_title"]: m["parent_id"] for _, m in chunks if not m.get("is_parent")}
    assert by_sec["退货政策"] == by_sec["退货流程"]
    assert any(m["section_title"] == "配送" for _, m in children)


def test_markdown_child_size_bounded(tmp_path):
    body = "句子。" * 400
    md = f"## 长小节\n\n{body}\n"
    proc = DocumentProcessor(chunk_size=400, chunk_overlap=40)
    chunks, _ = proc.process(_write(tmp_path, "b.md", md), "d2", "b.md")
    children = [t for t, m in chunks if not m.get("is_parent")]
    assert children
    for c in children:
        assert len(c) <= 420, f"child too large: {len(c)}"


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
    assert all("Q:" in b and "A:" in b for b in blobs)


def test_table_becomes_standalone_chunk_and_not_duplicated(tmp_path):
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
    non_table_chunks = [t for t, m in chunks if not m.get("is_table") and not m.get("is_parent")]
    assert all("华东 | 1 天" not in t for t in non_table_chunks)


def test_generic_chapter_paragraph_window(tmp_path):
    txt = (
        "第一章 概述\n\n本文介绍系统架构。模型层负责推理。\n\n"
        "第二章 部署\n\n支持容器化部署，推荐 Kubernetes。\n"
    )
    proc = DocumentProcessor()
    chunks, chapter_metas = proc.process(_write(tmp_path, "p.txt", txt), "d5", "p.txt")
    _assert_required_meta(chunks)
    assert any(m.get("is_parent") and m["section_title"] == "第一章 概述" for _, m in chunks)
    assert len(chapter_metas) == 2


def test_department_override(tmp_path):
    md = "## 小节\n\n正文。\n"
    proc = DocumentProcessor(department="sales")
    chunks, _ = proc.process(_write(tmp_path, "x.md", md), "d6", "x.md")
    assert all(m["department"] == "sales" for _, m in chunks)


def test_chunk_offsets_point_to_source(tmp_path):
    md = "## 安装\n\n第一步准备环境。\n\n第二步执行安装命令。\n"
    path = _write(tmp_path, "o.md", md)
    proc = DocumentProcessor()
    text = proc.load(path)
    chunks, _ = proc.process(path, "d7", "o.md")
    children = [(t, m) for t, m in chunks if not m.get("is_parent")]
    assert children
    for chunk_text, meta in children:
        s = meta.get("chunk_start", -1)
        e = meta.get("chunk_end", -1)
        assert 0 <= s < e <= len(text)
        assert chunk_text[:10] in text[max(0, s - 5): min(len(text), e + 5)]


def test_chunk_size_config_affects_generic_window(tmp_path):
    para = "A" * 220
    txt = f"{para}\n\n{para}\n\n{para}\n"
    proc = DocumentProcessor(chunk_size=240, chunk_overlap=30)
    chunks, _ = proc.process(_write(tmp_path, "w.txt", txt), "d8", "w.txt")
    children = [t for t, m in chunks if not m.get("is_parent")]
    assert len(children) >= 2
    assert all(len(c) <= 260 for c in children)


def test_process_steps_chunking_and_metadata(tmp_path):
    sop = (
        "流程名称：员工请假审批流程\n"
        "步骤1：提交申请\n角色：员工\n输入：请假单\n输出：申请记录\n下一步：主管审批\n\n"
        "步骤2：主管审批\n角色：主管\n前置条件：收到申请\n分支：同意进入人事备案；驳回返回修改\n\n"
        "步骤3：人事备案\n角色：人事\n输出：归档完成\n"
    )
    proc = DocumentProcessor()
    chunks, chapter_metas = proc.process(_write(tmp_path, "sop.txt", sop), "d9", "sop.txt")
    _assert_required_meta(chunks)
    step_chunks = [(t, m) for t, m in chunks if m.get("is_process_step")]
    assert len(step_chunks) == 3
    assert all(m.get("process_name") == "员工请假审批流程" for _, m in step_chunks)
    assert [m.get("step_no") for _, m in step_chunks] == ["1", "2", "3"]
    assert step_chunks[0][1].get("next_step") == "2"
    assert step_chunks[1][1].get("prev_step") == "1"
    assert step_chunks[1][1].get("role") == "主管"
    assert "驳回返回修改" in (step_chunks[1][1].get("branch") or step_chunks[1][0])
    assert chapter_metas and chapter_metas[0].get("chapter_title") == "员工请假审批流程"
