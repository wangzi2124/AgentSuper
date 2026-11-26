"""Question policy: prompt guidance, agent allow/deny, simulated-model loop."""

from __future__ import annotations

import asyncio
import json

from agents.registry import parse_agent_file, question_allowed
from app.session import context as session_context
from app.session import repository as repo
from execution.tool_registry import ToolRegistry
from execution.tools import question_tools
from graph.main_graph import default_registry
from graph.nodes.base import build_tool_defs, run_tool_loop
from models.router import ChatResult, ToolCall
from prompts.loader import PromptLoader


def test_shared_prompt_requires_recommended_options_when_asking():
    head = PromptLoader().build_agent_prompt("file_agent", "openai/gpt-4o")
    assert "ask_question" in head
    assert "recommended=true" in head
    assert "2–4" in head or "2-4" in head


def test_agent_question_defaults_and_explicit_deny(tmp_path):
    primary = tmp_path / "build.md"
    primary.write_text("---\nmode: primary\n---\n", encoding="utf-8")
    sub = tmp_path / "explore.md"
    sub.write_text(
        "---\nmode: subagent\ntools: [read_file, ls]\nquestion: deny\n---\n",
        encoding="utf-8",
    )
    allowed = tmp_path / "planner.md"
    allowed.write_text(
        "---\nmode: subagent\nquestion: allow\n---\n", encoding="utf-8"
    )

    primary_def = parse_agent_file(primary, "project")
    sub_def = parse_agent_file(sub, "project")
    allowed_def = parse_agent_file(allowed, "project")

    assert primary_def is not None and question_allowed(primary_def.question)
    assert sub_def is not None and not question_allowed(sub_def.question)
    assert allowed_def is not None and question_allowed(allowed_def.question)

    registry, _ = default_registry(with_shell=False)
    assert question_tools.QUESTION_TOOL_NAME in registry.names()
    scoped = registry.excluding({question_tools.QUESTION_TOOL_NAME})
    assert question_tools.QUESTION_TOOL_NAME not in scoped.names()


def test_tool_schema_exposes_structured_options():
    registry, _ = default_registry(with_shell=False)
    defs = build_tool_defs(registry)
    question = next(
        item for item in defs if item["function"]["name"] == "ask_question"
    )
    options = question["function"]["parameters"]["properties"]["options"]
    assert options["type"] == "array"
    assert set(options["items"]["properties"]) == {"label", "description", "recommended"}
    assert "推荐" in question["function"]["description"]


class _AskThenAnswerModel:
    """Scripted fake model: one structured question tool call, then an answer."""

    tier = "fake"

    def __init__(self) -> None:
        self.round = 0
        self.tool_result = ""

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_question",
                        name="ask_question",
                        arguments=json.dumps(
                            {
                                "question": "下一步怎么做？",
                                "header": "实现方向",
                                "options": [
                                    {"label": "先补测试", "description": "先写契约测试", "recommended": True},
                                    {"label": "直接实现", "description": "立即写代码"},
                                ],
                                "custom": False,
                                "timeout_seconds": 5,
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                finish_reason="tool-calls",
            )
        last = messages[-1]
        self.tool_result = str(last.get("content") or "")
        return ChatResult(content="收到，按推荐项继续", finish_reason="stop")


def test_simulated_model_question_answer_loop(monkeypatch):
    state: dict = {}
    created: dict = {}

    def create_form(sid, fid, fields, title=""):
        created["id"] = fid
        created["fields"] = fields
        state[fid] = {
            "id": fid, "session_id": sid, "state": "pending",
            "response": {}, "revision": 0,
        }
        return {
            "id": fid, "session_id": sid, "title": title,
            "fields": fields, "revision": 0,
        }

    def reply_form(sid, fid, response, expected_revision=None):
        state[fid].update(response=response, state="replied", revision=1)
        return True

    def list_forms(sid):
        for item in state.values():
            if item["state"] == "pending":
                item.update(response={"answer": "先补测试"}, state="replied")
        return list(state.values())

    monkeypatch.setattr(repo, "create_form", create_form)
    monkeypatch.setattr(repo, "list_forms", list_forms)
    monkeypatch.setattr(repo, "reply_form", reply_form)
    monkeypatch.setattr(repo, "cancel_form", lambda sid, fid, expected_revision=None: True)
    session_context.set_session_context("ses_sim", None)

    registry = ToolRegistry()
    registry.register_function(
        question_tools.ask_question,
        name="ask_question",
        description=question_tools.ask_question.__doc__ or "",
        scope="global",
        parameters=question_tools.QUESTION_TOOL_PARAMETERS,
    )

    async def run():
        model = _AskThenAnswerModel()
        result = await asyncio.wait_for(
            run_tool_loop(model, [{"role": "user", "content": "继续"}], registry),
            timeout=10,
        )
        assert created["fields"][0]["option_details"][0]["recommended"] is True
        return model, result

    model, result = asyncio.run(run())
    assert result["text"] == "收到，按推荐项继续"
    assert "先补测试" in model.tool_result
    assert result["rounds"] == 2
