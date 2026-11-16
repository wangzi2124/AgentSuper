"""流程型知识库分块单元测试。"""

import os

from cognition.document_processor import DocumentProcessor


def _write(tmp_path, name, text):
    p = os.path.join(str(tmp_path), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def test_process_doc_long_step_is_hard_split_with_metadata(tmp_path):
    long_body = "角色：审核员\n输入：材料\n输出：结果\n" + ("执行检查项并记录。" * 120)
    sop = f"流程名称：超长流程\n步骤1：审核材料\n{long_body}\n步骤2：归档\n角色：文员\n输出：完成\n"
    proc = DocumentProcessor(chunk_size=220, chunk_overlap=20)
    chunks, _ = proc.process(_write(tmp_path, "long_sop.txt", sop), "p1", "long_sop.txt")
    step1 = [(t, m) for t, m in chunks if m.get("is_process_step") and m.get("step_no") == "1"]
    assert len(step1) >= 2
    assert all(len(t) <= 240 for t, _ in step1)
    assert all(m.get("process_name") == "超长流程" for _, m in step1)
    assert all(m.get("role") == "审核员" for _, m in step1)
    assert all(m.get("next_step") == "2" for _, m in step1)
    # offsets present
    assert all(m.get("chunk_end", 0) > m.get("chunk_start", -1) >= 0 for _, m in step1)
