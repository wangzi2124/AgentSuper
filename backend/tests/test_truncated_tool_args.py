"""工具参数被输出上限截断 → 静默写出损坏文件的回归测试。

实测事故（2026-10-03，「你能在 D 盘写个俄罗斯方块游戏吗」，deepseek-v4-flash）：
  第 5 轮 finish_reason=length、completion_tokens=8192（正好撞 LLM_MAX_TOKENS），
  带 1 个 tool_write_file、参数 24,528 字符 —— 即整个文件的代码被塞进一次调用。
  `parse_tool_args` → `parse_json_value` 把半截 JSON「修复」成合法 dict，
  于是 tool_write_file 拿着残缺 content 正常执行、回报成功，
  产出 24,877 字节的 tetris/game.js：结尾停在 `const dt = performance.now()`、
  花括号 +2 / 圆括号 +2 不配平、IIFE `})();` 缺失 —— 语法错误，浏览器直接白屏。

修复：`finish_reason == "length"` 且仍带 tool_calls 时一律**不执行**，
改回一条带「分段写」可操作指引的错误，让模型缩小 payload 重试。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _tc(name: str, args: str):
    return type("TC", (), {
        "id": "call_x",
        "function": type("F", (), {"name": name, "arguments": args})(),
    })()


def truncated_payload_content(raw: str) -> str:
    """从半截 JSON 里取出「模型原本想写的 content」，用于对比修复器是否动了它。"""
    marker = '"content": "'
    start = raw.index(marker) + len(marker)
    return raw[start:].rsplit('"', 1)[0].replace("\\\\n", "\\n")


def test_truncated_args_message_is_actionable():
    """回灌文案必须给出可操作的下一步，否则模型会原样重试同样大的 payload。"""
    from app.agent.graphmod.constants import _TRUNCATED_ARGS_ERROR
    msg = _TRUNCATED_ARGS_ERROR.format(tool="tool_write_file")
    assert "tool_append_file" in msg, "必须指引分段续写"
    assert "截断" in msg
    assert "已阻止执行" in msg


def test_parse_tool_args_would_have_accepted_truncated_json():
    """前提守卫：证明「坏 JSON 被修复成合法 dict」这条路径真实存在。

    若哪天 `parse_tool_args` 改成严格模式（半截 JSON 直接 None），本测试会失败 ——
    那意味着本 bug 的机理变了，`_args_truncated` 守卫虽然仍无害但已非必需。
    """
    from app.utils.json_repair import parse_tool_args
    truncated = '{"path": "a.js", "content": "let a = 1;\\nconsole.log(a)'  # 少了收尾
    args = parse_tool_args(truncated)
    assert args is not None, "json_repair 已不再修复半截 JSON，本回归的前提需重估"
    assert args.get("path") == "a.js"
    # 关键危害：修复器不是「丢弃」而是**替模型把缺失的收尾补上**（这里补了个 `)`），
    # 于是参数变成完全合法的 dict，工具无从分辨内容被砍过，静默写盘。
    assert args.get("content").startswith("let a = 1;\nconsole.log(a")
    assert args["content"] != truncated_payload_content(truncated)


def test_generate_blocks_tool_execution_when_args_truncated(monkeypatch):
    """核心回归：被截断的 tool_write_file **绝不能**被调用。"""
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

    executed: list[tuple[str, dict]] = []

    async def spy_tool(name, args, state=None):
        executed.append((name, args))
        return "工具输出"

    agent._execute_tool = spy_tool

    # 收尾提示会禁用工具（tools=None），这里不能断言最终 answer，只关心「有没有执行」
    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(
            tool_calls=[("tool_write_file",
                         json.dumps({"path": "game.js",
                                     "content": "let a = 1;"},
                                    ensure_ascii=False)[:-1])],
            finish_reason="length",
        ),
        FakeLLM().response(content="done"),
    ]
    agent._llm_call = llm

    asyncio.run(agent._generate(make_state(question="写个俄罗斯方块")))

    assert not executed, f"被截断的工具参数竞然被执行了: {executed}"


def test_generate_error_message_reaches_the_model(monkeypatch):
    """守卫必须把错误**回灌给模型**，否则它不知道该改用分段写。"""
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(
            tool_calls=[("tool_write_file",
                         json.dumps({"path": "game.js", "content": "let a = 1;"},
                                    ensure_ascii=False)[:-1])],
            finish_reason="length",
        ),
        FakeLLM().response(content="done"),
    ]
    seen = []

    async def spy_llm(model, messages, tool_defs, state=None):
        seen.append([dict(m) for m in messages])
        return await llm(model, messages, tool_defs, state=state)

    agent._llm_call = spy_llm

    async def noop_tool(name, args, state=None):
        raise AssertionError("不应执行任何工具")

    agent._execute_tool = noop_tool

    asyncio.run(agent._generate(make_state(question="写个俄罗斯方块")))

    assert len(seen) >= 2, "未续跑"
    blob = "\n".join(str(m.get("content") or "") for m in seen[1])
    assert "截断" in blob and "tool_append_file" in blob, (
        f"截断错误未回灌给模型: {blob[-400:]}")


def test_generate_normal_length_tool_calls_still_execute(monkeypatch):
    """反向守卫：正常（finish=tool_calls）必须照常执行，绝不能被误伤。"""
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _graphmod_support import FakeLLM, build_agent, make_state
    from app.config import settings
    import app.agent.graphmod.generate as gen_mod

    monkeypatch.setattr(settings, "max_context_tokens", 10_000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 1_000)
    monkeypatch.setattr(settings, "context_safety_ratio", 0.5)
    monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
    monkeypatch.setattr(settings, "step_summary_enabled", False)
    for n in ("record_model_call", "trace", "trace_messages"):
        monkeypatch.setattr(gen_mod, n, lambda *a, **k: None)

    agent = build_agent()
    monkeypatch.setattr(agent, "_build_tool_defs", lambda *a, **k: [])

    executed: list[str] = []

    async def spy_tool(name, args, state=None):
        executed.append(name)
        return "工具输出"

    agent._execute_tool = spy_tool

    llm = FakeLLM()
    llm.responses = [
        FakeLLM().response(tool_calls=[("tool_write_file", '{"path": "a.js"}')]),
        FakeLLM().response(content="done"),
    ]
    agent._llm_call = llm

    out = asyncio.run(agent._generate(make_state(question="写个文件")))
    assert executed == ["tool_write_file"], executed
    assert out["answer"] == "done"


def test_generate_source_gates_truncated_args_on_length_finish_reason():
    """静态守卫：截断判定必须挂在 finish_reason 上，不能误伤正常轮次。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/agent/graphmod/generate.py").read_text(
        encoding="utf-8")
    assert '_args_truncated = str(finish_reason) == "length"' in src
    assert "_TRUNCATED_ARGS_ERROR" in src