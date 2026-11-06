"""File evidence is a target-aware query over Tool Parts, not tool count."""

from __future__ import annotations

import json

from harness.file_evidence import (
    ACTION_PROMISE_RE,
    FileEvidenceDecision,
    FileTaskEvidence,
    has_evidence,
    has_prior_evidence,
    parse_file_task,
)
from harness.turn_parts import ToolPart, ToolPartStatus


def _part(name: str, arguments: dict, *, failed: bool = False) -> ToolPart:
    part = ToolPart(
        call_id="c1",
        name=name,
        agent_id="build",
        step_index=0,
        arguments=arguments,
    )
    part.status = ToolPartStatus.FAILED if failed else ToolPartStatus.COMPLETED
    return part


def test_parent_listing_is_not_evidence_for_readme():
    requirement = parse_file_task(r"先 ls C:\tool\AgentSuper，再查看 README.md 写的是什么")
    assert requirement is not None
    assert requirement.target == "README.md"
    assert "read" in requirement.actions

    listed = _part("ls", {"path": r"C:\tool\AgentSuper"})
    assert has_evidence([listed], requirement) is False

    read = _part("read_file", {"path": r"C:\tool\AgentSuper\README.md"})
    assert has_evidence([read], requirement) is True


def test_drive_target_matches_normalized_part_target():
    requirement = parse_file_task("在 D盘有哪些文件")
    assert requirement is not None
    assert requirement.target.lower() == "d:"

    part = _part("ls", {"path": "D:/"})
    assert has_evidence([part], requirement) is True


def test_generic_directory_phrase_does_not_become_a_target():
    requirement = parse_file_task("列出当前目录")
    assert requirement is not None
    assert requirement.target == ""

    part = _part("ls", {"path": "."})
    assert has_evidence([part], requirement) is True


def test_custom_read_tool_with_matching_target_is_evidence():
    requirement = parse_file_task("读取 secret.txt，然后只回复完成。")
    assert requirement is not None
    part = _part("tool_read_file", {"path": "secret.txt"})
    assert has_evidence([part], requirement) is True


def test_failed_part_is_not_evidence():
    requirement = parse_file_task("查看 README.md")
    assert requirement is not None
    part = _part("read_file", {"path": "README.md"}, failed=True)
    assert has_evidence([part], requirement) is False


def test_prior_parent_listing_does_not_satisfy_file_read():
    requirement = parse_file_task("查看 README.md 写了什么")
    assert requirement is not None
    history = [
        {"role": "user", "content": r"先 ls C:\tool\AgentSuper"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c0",
                    "type": "function",
                    "function": {
                        "name": "ls",
                        "arguments": json.dumps({"path": r"C:\tool\AgentSuper"}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "name": "ls", "content": "README.md"},
        {"role": "user", "content": "查看 README.md 写了什么"},
    ]
    assert has_prior_evidence(history, requirement) is False

    history[2] = {
        "role": "tool",
        "tool_call_id": "c0",
        "name": "read_file",
        "content": "# AgentSuper",
    }
    history[1]["tool_calls"][0]["function"] = {
        "name": "read_file",
        "arguments": json.dumps({"path": r"C:\tool\AgentSuper\README.md"}),
    }
    assert has_prior_evidence(history, requirement) is True


def test_action_promise_is_detected():
    assert ACTION_PROMISE_RE.search("我将读取 README.md，请稍等。")
    assert ACTION_PROMISE_RE.search("Let me read the file") is None


def test_file_task_evidence_makes_one_step_boundary_decision():
    evidence = FileTaskEvidence.from_messages(
        [{"role": "user", "content": "先 ls C:\\tool\\AgentSuper，再查看 README.md"}]
    )
    listed = _part("ls", {"path": r"C:\tool\AgentSuper"})
    read = _part("read_file", {"path": r"C:\tool\AgentSuper\README.md"})

    assert (
        evidence.decide([listed], "我还没有读取文件。")
        is FileEvidenceDecision.BLOCK
    )
    assert (
        evidence.decide(
            [listed],
            "我将读取 README.md，请稍等。",
            can_nudge=True,
        )
        is FileEvidenceDecision.NUDGE
    )
    assert (
        evidence.decide([read], "README.md 是项目说明。")
        is FileEvidenceDecision.SATISFIED
    )


def test_prior_evidence_satisfies_the_same_policy():
    evidence = FileTaskEvidence.from_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c0",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps(
                                {"path": r"C:\tool\AgentSuper\README.md"}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c0",
                "name": "read_file",
                "content": "# AgentSuper",
            },
            {"role": "user", "content": "查看 README.md 写了什么"},
        ]
    )
    assert evidence.decide([], "") is FileEvidenceDecision.SATISFIED
