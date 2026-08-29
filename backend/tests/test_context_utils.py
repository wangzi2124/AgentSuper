"""Unit tests for context-layer utilities and JSON repair.

Covers:
- app.utils.json_repair (parse_json_value / parse_tool_args / light fix / rebuild)
- app.context.token_counter (estimate / truncate / sanitize)
- app.context.budget (token budget math)
- app.context.tool_output (bounding / prune / cleanup)
- app.context.task_state (SQLite task persistence)
- app.context.compaction (select / summarize / fallback / truncate)
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from app.context import token_counter
from app.context import tool_output
from app.context import task_state
from app.context import compaction
from app.context import budget
from app.utils import json_repair


@pytest.fixture(autouse=True)
def deterministic_counter(monkeypatch):
    """Pin token estimator to the heuristic path so counts are deterministic."""
    monkeypatch.setattr(token_counter, "_encoder", False)
    monkeypatch.setattr(token_counter, "_correction", 1.13)
    yield


# ---------------------------------------------------------------------------
# json_repair
# ---------------------------------------------------------------------------


class TestParseJsonValue:
    def test_none_and_empty(self):
        assert json_repair.parse_json_value(None) is None
        assert json_repair.parse_json_value("") is None
        assert json_repair.parse_json_value("   ") is None

    def test_valid_direct(self):
        assert json_repair.parse_json_value('{"a": 1, "b": [1, 2]}') == {"a": 1, "b": [1, 2]}
        assert json_repair.parse_json_value("[1, 2, 3]") == [1, 2, 3]

    def test_code_fence(self):
        raw = '```json\n{"a": 1}\n```'
        assert json_repair.parse_json_value(raw) == {"a": 1}
        raw_plain = "prefix\n```\n{\"a\": 1}\n```\nsuffix"
        assert json_repair.parse_json_value(raw_plain) == {"a": 1}

    def test_trailing_comma_and_comments(self):
        raw = '{"a": 1, "b": [2, 3,],}'
        assert json_repair.parse_json_value(raw) == {"a": 1, "b": [2, 3]}
        raw_c = '{"a": 1, /* block */ "b": 2 // trailing\n}'
        assert json_repair.parse_json_value(raw_c) == {"a": 1, "b": 2}

    def test_python_literals(self):
        raw = '{"ok": True, "no": False, "none": None}'
        assert json_repair.parse_json_value(raw) == {"ok": True, "no": False, "none": None}

    def test_single_quotes(self):
        raw = "{'a': 'b'}"
        assert json_repair.parse_json_value(raw) == {"a": "b"}
        raw_inner = "{'a': 'say \"hi\"'}"
        assert json_repair.parse_json_value(raw_inner) == {"a": 'say "hi"'}  # product handles escaped \"

    def test_newline_inside_double_quote_string(self):
        raw = '{"old": "line1\nline2"}'
        assert json_repair.parse_json_value(raw) == {"old": "line1\nline2"}

    def test_rebuild_unbalanced(self):
        assert json_repair.parse_json_value('{"a": {') == {"a": {}}
        # !!! note: no way to avoid it via json, fine

    def test_rebuild_prose_before(self):
        assert json_repair.parse_json_value('Here you go: {"a": 1}') == {"a": 1}

    def test_rebuild_unterminated_string(self):
        assert json_repair.parse_json_value('{"a": "unterminated') == {"a": "unterminated"}

    def test_total_garbage(self):
        assert json_repair.parse_json_value("zzz not json at all") is None

    def test_parse_tool_args(self):
        assert json_repair.parse_tool_args('{"a": 1}') == {"a": 1}
        assert json_repair.parse_tool_args("{}") == {}
        assert json_repair.parse_tool_args("42") is None
        assert json_repair.parse_tool_args(None) is None


class TestJsonRepairInternals:
    def test_light_fix_tail_comma_only_breaks(self):
        text = json_repair._light_fix('{"a": 1}')
        assert text == '{"a": 1}'

    def test_rebuild_no_bracket_returns_text(self):
        assert json_repair._rebuild_structure("no brackets here") == "no brackets here"

    def test_rebuild_mixed_and_after_prose(self):
        rebuilt = json_repair._rebuild_structure('{"a": [1, 2] trailing prose')
        # rebuild strips inner spaces and keeps literal chars, closes unclosed brace
        assert rebuilt.startswith('{"a":[1,2]')
        assert rebuilt.endswith('}')


# ---------------------------------------------------------------------------
# token_counter
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_is_zero(self):
        assert token_counter.estimate_tokens("") == 0

    def test_heuristic_fallback(self):
        assert token_counter.estimate_tokens("abcd") == 1  # round(4/4*1.13) = 1

    def test_encoder(self, monkeypatch):
        class Enc:
            def encode(self, text):
                return list(range(7))

        monkeypatch.setattr(token_counter, "_encoder", Enc())
        assert token_counter.estimate_tokens("hello") == 8  # round(7*1.13)

    def test_encoder_failure_falls_back(self, monkeypatch):
        class Enc:
            def encode(self, text):
                raise ValueError("boom")

        monkeypatch.setattr(token_counter, "_encoder", Enc())
        assert token_counter.estimate_tokens("abcd") == 1


class TestMessageEstimates:
    def test_single_message_str(self):
        assert token_counter._estimate_single_message({"role": "user", "content": "abcd"}) == 5

    def test_single_message_multimodal(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "abcd"}, {"type": "image_url", "image_url": {"url": "x"}}]}
        assert token_counter._estimate_single_message(msg) == 5

    def test_single_message_tool_calls(self):
        msg = {"role": "assistant", "tool_calls": [
            {"id": "1", "function": {"name": "tool_ls", "arguments": '{"path": "a"}'}},
        ]}
        total = token_counter._estimate_single_message(msg)
        assert total >= 4 + 8 + 2  # overhead + call overhead + name + args tokens

    def test_messages_sum(self):
        msgs = [{"role": "user", "content": "abcd"}, {"role": "assistant", "content": "wxyz"}]
        assert token_counter.estimate_tokens_messages(msgs) == 10

    def test_count_alias(self):
        msgs = [{"role": "user", "content": "abcd"}]
        assert token_counter.count_message_tokens(msgs) == token_counter.estimate_tokens_messages(msgs)

    def test_estimate_tools(self):
        assert token_counter.estimate_tools(None) == 0
        assert token_counter.estimate_tools([]) == 0
        assert token_counter.estimate_tools([{"name": "tool_ls", "parameters": {"type": "object"}}]) > 0

    def test_estimate_tools_serialization_error(self):
        assert token_counter.estimate_tools([{"name": "x", "params": object()}]) == 0


class TestTruncateMessages:
    def _msg(self, role, text):
        return {"role": role, "content": text}

    def test_empty(self):
        assert token_counter.truncate_messages([]) == []

    def test_within_budget_unchanged(self):
        msgs = [self._msg("system", "sys"), self._msg("user", "hello world")]
        assert token_counter.truncate_messages(msgs, max_tokens=1000) is msgs

    def test_system_preserved_and_sentinel(self):
        msgs = [self._msg("system", "sys"), self._msg("user", "x" * 100), self._msg("user", "y" * 100)]
        # tiny budget → nothing fits beyond the system message
        result = token_counter.truncate_messages(msgs, max_tokens=5, reserve_tokens=0)
        assert result[0] == msgs[0]
        assert result[1]["role"] == "system" and "truncated" in result[1]["content"]
        assert len(result) == 2

    def test_no_system(self):
        msgs = [self._msg("user", "x" * 100), self._msg("user", "y" * 100)]
        result = token_counter.truncate_messages(msgs, max_tokens=5, reserve_tokens=0)
        assert result[0]["content"] == "[earlier messages truncated to fit context window]"
        assert msgs[-1] not in result


class TestSanitizeToolMessages:
    def test_empty(self):
        assert token_counter.sanitize_tool_messages([]) == []

    def test_keeps_complete_round(self):
        msgs = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "f"}}]},
            {"role": "tool", "content": "out", "tool_call_id": "c1"},
            {"role": "assistant", "content": "done"},
        ]
        assert token_counter.sanitize_tool_messages(msgs) == msgs

    def test_drops_orphan_tool(self):
        msgs = [{"role": "tool", "content": "out", "tool_call_id": "c9"}]
        assert token_counter.sanitize_tool_messages(msgs) == []

    def test_drops_tool_with_mismatched_id(self):
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "f"}}]},
            {"role": "tool", "content": "out", "tool_call_id": "c2"},
        ]
        result = token_counter.sanitize_tool_messages(msgs)
        assert all(m.get("role") != "tool" for m in result)

    def test_drops_incomplete_round(self):
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "function": {"name": "f"}},
                {"id": "b", "function": {"name": "g"}},
            ]},
            {"role": "tool", "content": "out", "tool_call_id": "a"},
            {"role": "user", "content": "next"},
        ]
        result = token_counter.sanitize_tool_messages(msgs)
        assert all(m.get("role") != "assistant" or not m.get("tool_calls") for m in result)
        assert [m["role"] for m in result] == ["user"]

    def test_keeps_two_response_round(self):
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "function": {"name": "f"}},
                {"id": "b", "function": {"name": "g"}},
            ]},
            {"role": "tool", "content": "o1", "tool_call_id": "a"},
            {"role": "tool", "content": "o2", "tool_call_id": "b"},
        ]
        assert token_counter.sanitize_tool_messages(msgs) == msgs


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------


class TestBudget:
    def test_usable(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "max_context_tokens", 1000)
        monkeypatch.setattr(settings, "context_reserve_tokens", 200)
        assert budget.usable_context_tokens() == 800
        monkeypatch.setattr(settings, "context_reserve_tokens", 99999)
        assert budget.usable_context_tokens() == 0

    def test_threshold_explicit(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "compaction_threshold_tokens", 500)
        assert budget.compaction_threshold_tokens() == 500

    def test_threshold_ratio(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "compaction_threshold_tokens", 0)
        monkeypatch.setattr(settings, "max_context_tokens", 1000)
        monkeypatch.setattr(settings, "context_reserve_tokens", 200)
        monkeypatch.setattr(settings, "compaction_threshold_ratio", 0.5)
        assert budget.compaction_threshold_tokens() == 400

    def test_prune_protect_and_minimum(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "compaction_threshold_tokens", 500)
        monkeypatch.setattr(settings, "tool_output_protect_tokens", 40000)
        assert budget.prune_protect_tokens() == 250
        assert budget.prune_minimum_tokens() == 125
        monkeypatch.setattr(settings, "tool_output_protect_tokens", 100)
        assert budget.prune_protect_tokens() == 100
        assert budget.prune_minimum_tokens() == 50


# ---------------------------------------------------------------------------
# tool_output
# ---------------------------------------------------------------------------


class TestBoundToolOutput:
    def test_empty_returns_as_is(self):
        assert tool_output.bound_tool_output("") == ""

    def test_small_output_unchanged(self):
        text = "hello\nworld"
        assert tool_output.bound_tool_output(text) == text

    def test_line_truncation_no_save(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        text = "\n".join("line %d" % i for i in range(10))
        limits = tool_output.ToolOutputLimits(max_lines=3, max_bytes=10_000_000)
        out = tool_output.bound_tool_output(text, tool_name="", limits=limits, save_full_output=True)
        assert "showed 3/10 lines" in out
        assert out.endswith("]")
        assert "[full output saved" not in out

    def test_byte_truncation(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        out = tool_output.bound_tool_output("a" * 500, limits=tool_output.ToolOutputLimits(max_lines=999, max_bytes=100))
        assert "100/500 bytes" in out

    def test_tight_limits_grep(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        out = tool_output.bound_tool_output("a" * 20_000, tool_name="tool_grep")
        assert "16384/20000 bytes" in out

    def test_saved_hint_and_continuation(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "C:/trunc/full.txt")
        out = tool_output.bound_tool_output("a" * 1000, limits=tool_output.ToolOutputLimits(max_bytes=50))
        assert "; full output saved to C:/trunc/full.txt]" in out
        assert "tool_read_file with offset" in out

    def test_custom_toolname_limits_apply(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        limits = tool_output.ToolOutputLimits()
        limits.tight_limits = {"tool_ls": (5, 1024)}
        text = "\n".join("f%d" % i for i in range(20))
        out = tool_output.bound_tool_output(text, tool_name="tool_ls", limits=limits)
        assert "showed 5/20 lines" in out


class TestWriteTruncatedAndCleanup:
    def test_write_truncated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tool_output, "_truncation_dir", lambda: tmp_path)
        path = tool_output._write_truncated("full text here")
        assert path.startswith(str(tmp_path))
        assert Path(path).read_text(encoding="utf-8") == "full text here"

    def test_write_truncated_oserror(self, monkeypatch, tmp_path):
        class FakePath(Path):
            def write_text(self, *a, **k):
                raise OSError("disk full")

        monkeypatch.setattr(tool_output, "Path", FakePath)
        monkeypatch.setattr(tool_output, "_truncation_dir", lambda: FakePath(str(tmp_path)))
        assert tool_output._write_truncated("x") == ""

    def test_cleanup_truncated(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tool_output, "_truncation_dir", lambda: tmp_path)
        (tmp_path / "tool_old.txt").write_text("old", encoding="utf-8")
        (tmp_path / "tool_new.txt").write_text("new", encoding="utf-8")
        old_f = tmp_path / "tool_old.txt"
        old_t = time.time() - 10000
        os.utime(old_f, (old_t, old_t))
        tool_output.cleanup_truncated(retention_seconds=3600)
        assert not old_f.exists()
        assert (tmp_path / "tool_new.txt").exists()

    def test_cleanup_no_dir(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope"
        monkeypatch.setattr(tool_output, "_truncation_dir", lambda: missing)
        tool_output.cleanup_truncated()

    def test_cleanup_dir_error(self, monkeypatch):
        def boom():
            raise OSError("nope")
        monkeypatch.setattr(tool_output, "_truncation_dir", boom)
        tool_output.cleanup_truncated()

    def test_estimate_output_tokens(self):
        assert tool_output.estimate_output_tokens("") == 0
        assert tool_output.estimate_output_tokens("abcd") == 1


class TestPruneToolOutputs:
    def _tool(self, content, tool_name="tool_read_file", tool_call_id="c"):
        return {"role": "tool", "content": content, "tool_name": tool_name, "tool_call_id": tool_call_id}

    def test_empty(self):
        assert tool_output.prune_tool_outputs([]) == []

    def test_no_tail_rounds_protects_all(self):
        msgs = [self._tool("x" * 500)]
        assert tool_output.prune_tool_outputs(msgs) is msgs

    def test_below_minimum_returns_same(self):
        msgs = [
            self._tool("short"),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "f"}}]},
        ]
        assert tool_output.prune_tool_outputs(msgs, protect_tokens=10000, minimum_tokens=1000) is msgs

    def test_prunes_and_stubs(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "C:/trunc/saved.txt")
        msgs = [self._tool("x" * 2000, tool_name="tool_read_file") for _ in range(12)]
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "f"}}]})
        result = tool_output.prune_tool_outputs(msgs, protect_tokens=500, minimum_tokens=100, tail_turns=1)
        assert result is not msgs
        assert any(str(m.get("content", "")).startswith("[tool output pruned") for m in result)
        assert "[tool output pruned to save context space: tool_read_file" in result[0]["content"]
        assert "Full output saved to: C:/trunc/saved.txt" in result[0]["content"]

    def test_checkpoint_stops_scan(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        old = self._tool("y" * 2000)
        msgs = [
            old,
            {"role": "system", "content": "[Task checkpoint — conversation compacted]"},
            self._tool("x" * 2000),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "f"}}]},
        ]
        result = tool_output.prune_tool_outputs(msgs, protect_tokens=200, minimum_tokens=100, tail_turns=1)
        assert result[0] is old  # behind checkpoint untouched
        assert str(result[2]["content"]).startswith("[tool output pruned")

    def test_stub_without_toolname(self, monkeypatch):
        monkeypatch.setattr(tool_output, "_write_truncated", lambda text: "")
        msgs = [{"role": "tool", "content": "x" * 2000, "tool_call_id": "c1"}]
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "f"}}]})
        result = tool_output.prune_tool_outputs(msgs, protect_tokens=200, minimum_tokens=100, tail_turns=1)
        assert result[0]["content"].startswith("[tool output pruned")
        assert "~" in result[0]["content"]


# ---------------------------------------------------------------------------
# task_state
# ---------------------------------------------------------------------------


@pytest.fixture
def task_db(tmp_path, monkeypatch):
    monkeypatch.setattr(task_state, "DB_PATH", tmp_path / "tasks.db")
    task_state._thread_local.conn = None
    yield task_state
    task_state._thread_local.conn = None


class TestTaskState:
    def test_save_load_roundtrip(self, task_db):
        ts = task_state.TaskState(conversation_id="conv1")
        ts.step = 3
        ts.total_tokens = 1234
        ts.tool_calls_count = 5
        ts.mark_completed()
        loaded = task_state.TaskState.load(ts.task_id)
        assert loaded is not None
        assert loaded.conversation_id == "conv1"
        assert loaded.step == 3
        assert loaded.total_tokens == 1234
        assert loaded.tool_calls_count == 5
        assert loaded.status == "completed"

    def test_load_missing(self, task_db):
        assert task_state.TaskState.load("nope") is None

    def test_mutations(self, task_db):
        ts = task_state.TaskState(conversation_id="c2", status="running")
        ts.increment_step()
        ts.add_tokens(100)
        ts.add_tokens(50)
        ts.increment_tool_calls(2)
        ts.record_compaction()
        d = ts.to_dict()
        assert d["step"] == 1
        assert d["total_tokens"] == 150
        assert d["tool_calls_count"] == 2
        assert task_state.TaskState.load(ts.task_id).last_compaction_step == 1
        ts.mark_failed("boom")
        assert task_state.TaskState.load(ts.task_id).status == "failed"

    def test_upsert_same_id(self, task_db):
        ts = task_state.TaskState(conversation_id="c3")
        ts.save()
        task_state.TaskState.load(ts.task_id).save()
        rows = task_state.TaskState.list_by_conversation("c3")
        assert len(rows) == 1

    def test_to_dict_fields(self, task_db):
        ts = task_state.TaskState(conversation_id="c4", status="running")
        d = ts.to_dict()
        assert set(d) == {"task_id", "conversation_id", "status", "step", "total_tokens", "tool_calls_count"}

    def test_list_ordering(self, task_db):
        a = task_state.TaskState(conversation_id="c5")
        b = task_state.TaskState(conversation_id="c5")
        a.save()
        time.sleep(0.02)
        b.save()
        ids = [t.task_id for t in task_state.TaskState.list_by_conversation("c5")]
        assert ids[0] == b.task_id

    def test_cleanup_old_removes_old(self, task_db):
        ts = task_state.TaskState(conversation_id="c6")
        ts.mark_completed()
        conn = task_state._get_db()
        conn.execute(
            "INSERT INTO tasks (id, conversation_id, status, step, created_at, updated_at) "
            "VALUES (?, ?, 'completed', 0, datetime('now', '-14 days'), datetime('now', '-14 days'))",
            ("stale_task", "c6"),
        )
        conn.commit()
        task_state.cleanup_old_tasks()
        assert task_state.TaskState.load("stale_task") is None
        assert task_state.TaskState.load(ts.task_id) is not None

    def test_cleanup_error(self, task_db, monkeypatch):
        def boom():
            raise sqlite3.Error("locked")
        monkeypatch.setattr(task_state, "_get_db", boom)
        task_state.cleanup_old_tasks()


# ---------------------------------------------------------------------------
# compaction
# ---------------------------------------------------------------------------


class TestCompactionHelpers:
    def test_is_checkpoint(self):
        assert compaction.is_checkpoint({"role": "system", "content": "[Task checkpoint — x]"})
        assert not compaction.is_checkpoint({"role": "system", "content": "hello"})
        assert not compaction.is_checkpoint({"role": "user", "content": "[Task checkpoint — x]"})
        assert not compaction.is_checkpoint({})

    def test_truncate_tool_output(self):
        text = "x" * 100
        assert compaction._truncate_tool_output(text) == text
        out = compaction._truncate_tool_output(text, max_chars=40)
        assert out.startswith("x" * 40)
        assert "[Tool output truncated for compaction: omitted 60 chars]" in out
        assert compaction._truncate_tool_output(text, max_chars=0) == text

    def test_summary_of(self):
        assert compaction._summary_of({"role": "system", "content": "no newline"}) is None
        base = "[Task checkpoint — x]\n\nSummary body"
        assert compaction._summary_of({"role": "system", "content": base}) == "Summary body"
        with_preserved = base + "\n\n[Recent messages preserved below — do not repeat]"
        assert compaction._summary_of({"role": "system", "content": with_preserved}) == "Summary body"

    def test_previous_summary_of(self):
        assert compaction.previous_summary_of([]) is None
        msgs = [
            {"role": "system", "content": "[Task checkpoint — x]\n\nOld\n\n[Recent messages preserved]"},
            {"role": "user", "content": "hi"},
        ]
        assert compaction.previous_summary_of(msgs) == "Old"
        no_checkpoint = [{"role": "user", "content": "hi"}]
        assert compaction.previous_summary_of(no_checkpoint) is None


class TestContextCompactor:
    def test_defaults(self):
        c = compaction.ContextCompactor()
        assert c.threshold == compaction.DEFAULT_COMPACTION_THRESHOLD
        assert c.tail_turns == 2
        assert c.preserve_recent_tokens == 8000

    def _small_user(self, text="hi"):
        return {"role": "user", "content": text}

    def test_should_compact(self):
        c = compaction.ContextCompactor(threshold=100)
        assert not c.should_compact([self._small_user()])
        assert c.should_compact([self._small_user("x" * 1000)])

    def test_select_empty(self):
        c = compaction.ContextCompactor()
        assert c._select([], 1000) == ([], [])

    def test_select_no_user(self):
        c = compaction.ContextCompactor()
        msgs = [{"role": "assistant", "content": "a"}]
        assert c._select(msgs, 1000) == (msgs, [])

    def test_select_all_kept(self):
        c = compaction.ContextCompactor(preserve_recent_tokens=100000)
        msgs = [self._small_user("a"), {"role": "assistant", "content": "b"}]
        head, tail = c._select(msgs, 100000)
        assert head == []
        assert tail == msgs

    def test_select_fallback_last_user(self):
        c = compaction.ContextCompactor(preserve_recent_tokens=0, tail_turns=1)
        msgs = [self._small_user("first"), {"role": "assistant", "content": "x"}, self._small_user("second")]
        head, tail = c._select(msgs, 0)
        assert head == msgs[:2]
        assert tail == msgs[2:]

    def test_select_fallback_first_user(self):
        c = compaction.ContextCompactor(preserve_recent_tokens=0, tail_turns=2)
        msgs = [self._small_user("only"), {"role": "assistant", "content": "x"}]
        head, tail = c._select(msgs, 0)
        assert head == msgs
        assert tail == []

    def test_select_single_message(self):
        c = compaction.ContextCompactor(preserve_recent_tokens=0)
        msgs = [self._small_user("only")]
        head, tail = c._select(msgs, 0)
        assert head == msgs
        assert tail == []

    def test_split_suffix(self):
        c = compaction.ContextCompactor()
        msgs = [self._small_user("a"), {"role": "assistant", "content": "b"}, self._small_user("c")]
        boundaries = sorted({0, 2})
        assert c._split_suffix(msgs, 0, 3, boundaries, 100000) == 2
        assert c._split_suffix(msgs, 0, 3, boundaries, 1) is None

    def test_ensure_within_budget_ok(self, monkeypatch):
        c = compaction.ContextCompactor(threshold=100000)
        msgs = [self._small_user("hi")]
        assert c._ensure_within_budget(msgs) is msgs

    def test_ensure_within_budget_prunes(self, monkeypatch):
        def fake_prune(messages, protect_tokens=0, minimum_tokens=0, tail_turns=0):
            return [m for m in messages if m["role"] == "user"] + [{"role": "tool", "content": "[stub]"}]

        monkeypatch.setattr("app.context.tool_output.prune_tool_outputs", fake_prune)
        c = compaction.ContextCompactor(threshold=0)
        msgs = [{"role": "tool", "content": "x" * 5000}]
        out = c._ensure_within_budget(msgs)
        assert any(m.get("content") == "[stub]" for m in out)

    def test_fallback_truncate(self):
        c = compaction.ContextCompactor(threshold=40)
        msgs = [{"role": "system", "content": "sys"}, self._small_user("a" * 400), self._small_user("b")]
        out = c._fallback_truncate(msgs)
        assert out[0] == {"role": "system", "content": "sys"}
        assert out[1]["content"] == "[earlier messages truncated to fit context window]"
        assert out[-1] == msgs[-1]

    async def test_compact_empty(self):
        c = compaction.ContextCompactor()
        assert await c.compact([]) == []

    async def test_compact_all_system(self):
        c = compaction.ContextCompactor()
        msgs = [{"role": "system", "content": "sys"}]
        assert await c.compact(msgs) is msgs

    async def test_compact_no_head(self):
        c = compaction.ContextCompactor(preserve_recent_tokens=100000)
        msgs = [self._small_user("hi"), {"role": "assistant", "content": "x"}]
        assert await c.compact(msgs) is msgs

    async def test_compact_success(self, monkeypatch):
        async def fake_summarize(self, messages, previous_summary):
            assert previous_summary is None
            return "Summary body"

        monkeypatch.setattr(compaction.ContextCompactor, "_summarize", fake_summarize)
        c = compaction.ContextCompactor(preserve_recent_tokens=1)
        msgs = [
            {"role": "system", "content": "sys"},
            self._small_user("first question"),
            {"role": "assistant", "content": "answer"},
            self._small_user("second question"),
        ]
        out = await c.compact(msgs)
        assert out[0] == {"role": "system", "content": "sys"}
        assert out[1]["role"] == "system" and out[1]["content"].startswith("[Task checkpoint")
        assert "Summary body" in out[1]["content"]
        assert out[-1]["content"] == "second question"
        assert out[-1]["role"] == "user"

    async def test_compact_summarize_empty_falls_back(self, monkeypatch):
        async def fake_summarize(self, messages, previous_summary):
            return ""

        monkeypatch.setattr(compaction.ContextCompactor, "_summarize", fake_summarize)
        c = compaction.ContextCompactor(preserve_recent_tokens=0, threshold=5)
        msgs = [self._small_user("a" * 50), self._small_user("b")]
        out = await c.compact(msgs)
        assert any(m.get("content") == "[earlier messages truncated to fit context window]" for m in out)

    async def test_summarize_success(self, monkeypatch):
        import types

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=50),
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="SUMMARY OK"))],
            )

        captured = {}
        monkeypatch.setattr(compaction.litellm, "acompletion", fake_acompletion)
        monkeypatch.setattr(compaction, "record_model_call", lambda *a, **k: None)
        c = compaction.ContextCompactor(model="deepseek/test", api_key="k", api_base="https://x")
        msgs = [self._small_user("hello world")]
        result = await c._summarize(msgs, previous_summary="Prev")
        assert result == "SUMMARY OK"
        assert captured["model"] == "deepseek/test"
        assert captured["api_key"] == "k"
        assert captured["api_base"] == "https://x"
        assert "<previous-summary>" in captured["messages"][0]["content"]

    async def test_summarize_failure(self, monkeypatch):
        async def fake_acompletion(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(compaction.litellm, "acompletion", fake_acompletion)
        c = compaction.ContextCompactor()
        assert await c._summarize([self._small_user("hi")], None) == ""

    async def test_summarize_truncates_tool_output(self, monkeypatch):
        import types

        captured = {}

        async def fake_acompletion(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return types.SimpleNamespace(
                usage=None,
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))],
            )

        monkeypatch.setattr(compaction.litellm, "acompletion", fake_acompletion)
        monkeypatch.setattr(compaction, "record_model_call", lambda *a, **k: None)
        c = compaction.ContextCompactor()
        msgs = [{"role": "tool", "content": "a" * 5000}]
        await c._summarize(msgs, None)
        assert "[Tool output truncated for compaction: omitted 3000 chars]" in captured["prompt"]

    async def test_summarize_multimodal_and_long(self, monkeypatch):
        import types

        captured = {}

        async def fake_acompletion(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return types.SimpleNamespace(
                usage=None,
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))],
            )

        monkeypatch.setattr(compaction.litellm, "acompletion", fake_acompletion)
        monkeypatch.setattr(compaction, "record_model_call", lambda *a, **k: None)
        c = compaction.ContextCompactor()
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "plain text"}]},
        ]
        # many tool messages: each truncated to 2000 chars → conversation_text
        # grows past the 16k-token budget and triggers head/tail collapse
        msgs += [{"role": "tool", "content": "z" * 100000} for _ in range(35)]
        await c._summarize(msgs, None)
        assert "[user]: plain text" in captured["prompt"]
        assert "CONVERSATION HISTORY:" in captured["prompt"]
        assert "... [middle portion omitted] ..." in captured["prompt"]