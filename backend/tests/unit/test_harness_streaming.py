from harness.delimiters import ARGS_CLOSE, ARGS_OPEN, NAME_CLOSE, NAME_OPEN
from harness.streaming import PreludeSuppressor, StreamToolCallParser


def test_emits_call_as_soon_as_args_close():
    p = StreamToolCallParser()
    assert p.feed("正在读取") == [{"type": "text", "text": "正在读取"}]
    assert p.feed(NAME_OPEN + "tool_read") == []
    events = p.feed("_file" + NAME_CLOSE + "\n")
    assert events == []
    events = p.feed(ARGS_OPEN + '{"path": "a.txt"}')
    assert events == []
    events = p.feed(ARGS_CLOSE)
    assert len(events) == 1
    assert events[0]["type"] == "tool_call"
    assert events[0]["call"].name == "tool_read_file"
    assert events[0]["call"].parsed == {"path": "a.txt"}
    assert p.finalize() == []


def test_delimiter_split_across_chunks_not_leaked_as_text():
    p = StreamToolCallParser()
    events = p.feed("abc" + NAME_OPEN[:1])
    assert events == [{"type": "text", "text": "abc"}]
    events = p.feed(NAME_OPEN[1:] + "tool_x" + NAME_CLOSE + ARGS_OPEN + "{}" + ARGS_CLOSE)
    assert len(events) == 1
    assert events[0]["call"].name == "tool_x"


def test_multiple_calls_in_one_chunk():
    text = (
        f"{NAME_OPEN}a{NAME_CLOSE}{ARGS_OPEN}{{}}{ARGS_CLOSE}"
        f"{NAME_OPEN}b{NAME_CLOSE}{ARGS_OPEN}{{\"x\": 1}}{ARGS_CLOSE}尾部"
    )
    p = StreamToolCallParser()
    events = p.feed(text)
    calls = [e["call"] for e in events if e["type"] == "tool_call"]
    assert [c.name for c in calls] == ["a", "b"]
    assert {"type": "text", "text": "尾部"} in events


def test_unfinished_call_is_dropped():
    p = StreamToolCallParser()
    p.feed(NAME_OPEN + "tool_x" + NAME_CLOSE + ARGS_OPEN + '{"a":')
    assert p.finalize() == []


def test_bad_json_gives_error_call():
    p = StreamToolCallParser()
    events = p.feed(
        f"{NAME_OPEN}tool_x{NAME_CLOSE}{ARGS_OPEN}not json{ARGS_CLOSE}"
    )
    assert events[0]["call"].parsed is None
    assert events[0]["call"].error


def test_plain_stream_text_is_flushed():
    p = StreamToolCallParser()
    assert p.feed("hello") == [{"type": "text", "text": "hello"}]
    assert p.finalize() == []


def test_native_prelude_suppressed_when_marker_appears():
    s = PreludeSuppressor()
    assert s.feed("我先解释一下" * 20) != ""
    assert s.feed('前置文本 {"name": "tool_x", "arguments": {}}') == ""
    assert s.finalize() == ""


def test_native_prelude_flushed_without_marker():
    s = PreludeSuppressor()
    out = s.feed("纯文本回答") + s.finalize()
    assert out == "纯文本回答"


def test_native_prelude_holds_partial_marker():
    s = PreludeSuppressor()
    assert s.feed("abcdefgh" + '{"name"') == "abcdefgh"
    assert s.finalize() == '{"name"'
