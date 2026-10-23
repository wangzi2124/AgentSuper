from pathlib import Path

from graph.nodes.filter import TOOL_INTRO_TEMPLATE
from graph.nodes.plan import SELF_INTRO_MAIN, SMALLTALK_REPLIES

REQUIRED_PHRASES = [
    "RAG + Agent",
    "文档上传",
    "向量检索",
    "多模型对话",
    "Skills",
    "Plugins",
]


def test_fixed_replies_share_system_intro():
    assert TOOL_INTRO_TEMPLATE == SELF_INTRO_MAIN
    assert SMALLTALK_REPLIES["greeting"] == SELF_INTRO_MAIN
    for phrase in REQUIRED_PHRASES:
        assert phrase in SELF_INTRO_MAIN
    assert "文件操作专家" not in SELF_INTRO_MAIN
    assert "工作区助手" not in SELF_INTRO_MAIN


def test_file_agent_prompt_has_same_intro():
    prompt = Path(__file__).resolve().parents[2] / "prompts" / "system" / "file_agent.md"
    text = prompt.read_text(encoding="utf-8")
    assert "## 自我介绍（固定模板）" in text
    assert SELF_INTRO_MAIN.split("\n", 1)[0] in text
    assert "一句话介绍" in text
