"""Env-gated real-model E2E golden cases (design §11.6)."""

from __future__ import annotations

import pytest

from config import settings
from execution.filesystem import VirtualFileSystem
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.main_graph import build_main_graph, run_agent
from models.router import TierModel, reset_request_model, set_request_model

_MODEL = (settings.harness_smoke_model_a or "").strip()
_ENABLED = bool(_MODEL and settings.llm_api_key)

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="set harness_smoke_model_a + llm_api_key to run live E2E",
)


def _registry(tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="tool_read_file",
            description="Read a workspace text file.",
            fn=lambda path: (tmp_path / path).read_text(encoding="utf-8"),
            scope="location",
        )
    )
    return registry


@pytest.mark.asyncio
async def test_e2e01_read_file_and_answer_fact(tmp_path):
    (tmp_path / "note.txt").write_text("project code ORION-42", encoding="utf-8")
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        graph = build_main_graph(
            model=model,
            registry=_registry(tmp_path),
            vfs=VirtualFileSystem(root=str(tmp_path)),
            enable_task_tool=False,
        )
        out = await run_agent(
            "Read note.txt with tool_read_file and reply with the project code only.",
            graph=graph,
            thread_id="e2e01",
        )
    finally:
        reset_request_model(token)

    assert out.get("tool_rounds", 0) >= 1
    assert "ORION-42" in str(out.get("final_output", ""))


@pytest.mark.asyncio
async def test_e2e07_out_of_scope_does_not_crash(tmp_path):
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        graph = build_main_graph(
            model=model,
            registry=_registry(tmp_path),
            vfs=VirtualFileSystem(root=str(tmp_path)),
            enable_task_tool=False,
        )
        out = await run_agent(
            "Tell me the current weather on Mars without using tools.",
            graph=graph,
            thread_id="e2e07",
        )
    finally:
        reset_request_model(token)

    text = str(out.get("final_output", ""))
    assert text.strip()
    assert "Traceback" not in text


@pytest.mark.asyncio
async def test_e2e02_multi_step_file_task(tmp_path):
    (tmp_path / "note.txt").write_text("ORION-42", encoding="utf-8")
    registry = _registry(tmp_path)
    registry.register(
        ToolDefinition(
            name="tool_write_file",
            description="Write a workspace text file.",
            fn=lambda path, content: (tmp_path / path).write_text(content, encoding="utf-8") or "written",
            scope="location",
        )
    )
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        graph = build_main_graph(
            model=model,
            registry=registry,
            vfs=VirtualFileSystem(root=str(tmp_path)),
            enable_task_tool=False,
        )
        out = await run_agent(
            "Read note.txt, then write its exact content to out.txt using tools. Reply done.",
            graph=graph,
            thread_id="e2e02",
        )
    finally:
        reset_request_model(token)

    assert out.get("tool_rounds", 0) >= 2
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "ORION-42"


@pytest.mark.asyncio
async def test_e2e03_kb_answer_uses_retrieved_fact(tmp_path):
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        graph = build_main_graph(
            model=model,
            registry=_registry(tmp_path),
            vfs=VirtualFileSystem(root=str(tmp_path)),
            retrieve=lambda query: [
                {
                    "content": "Project code ORION-42.",
                    "metadata": {"source": "kb.md", "title": "KB"},
                }
            ],
            enable_task_tool=False,
        )
        out = await run_agent(
            "According to the knowledge base, what is the project code?",
            graph=graph,
            thread_id="e2e03",
            use_vector_db=True,
        )
    finally:
        reset_request_model(token)

    assert "ORION-42" in str(out.get("final_output", ""))


@pytest.mark.asyncio
async def test_e2e06_real_compression_preserves_fact():
    from langchain_core.messages import AIMessage, HumanMessage
    from harness.compression import compress_graph_messages

    messages = []
    for i in range(30):
        fact = " Critical: code ORION-42, file /work/app.py." if i == 0 else ""
        messages.append(HumanMessage(content=("filler " * 150) + f" step {i}" + fact, id=f"h{i}"))
        messages.append(AIMessage(content=("ok " * 100), id=f"a{i}"))
    messages.append(HumanMessage(content="Thanks, continue.", id="critical"))

    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        out = await compress_graph_messages(
            messages,
            existing_summary="",
            model=model,
            compress_count=0,
            keep_recent=2,
        )
    finally:
        reset_request_model(token)

    assert out.triggered is True
    assert "ORION-42" in out.summary or "/work/app.py" in out.summary


@pytest.mark.asyncio
async def test_e2e08_real_parallel_subagents(tmp_path):
    import asyncio
    from graph.subagent import SubAgent, run_subagent

    (tmp_path / "a.txt").write_text("ALPHA-11", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BETA-22", encoding="utf-8")
    registry = _registry(tmp_path)
    forest = SubAgent(
        name="file2",
        prompt_path="system/file_agent.md",
        tools=("tool_read_file",),
    )
    model = TierModel(tier="auto", timeout=120)

    token = set_request_model(_MODEL)
    try:
        results = await asyncio.gather(
            run_subagent(forest, "Read a.txt and reply with its content.", registry=registry, model=model),
            run_subagent(forest, "Read b.txt and reply with its content.", registry=registry, model=model),
        )
    finally:
        reset_request_model(token)

    texts = [r["text"] for r in results]
    assert any("ALPHA-11" in t for t in texts)
    assert any("BETA-22" in t for t in texts)
