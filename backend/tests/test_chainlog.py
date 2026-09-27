"""全链路日志（chain_logs）测试。

覆盖：
- 上下文绑定/嵌套/重置、payload 注入与反绑定（跨 AgentBus 场景）
- 落盘线程批量写入、trace 内 seq 单调、flush/stop
- 查询：entries/traces 聚合/trace_detail/stats/distinct_values/cleanup/clear
- 中间件：X-Trace-Id 生成与透传、跳过路径、异常落 http.error
- 事件镜像：agent/tool/permission 事件 → 链路节点
- HTTP API：/api/logs 六个端点 + 管理员清理鉴权

单测强制 sqlite + 临时 DB（与仓库其它测试一致：monkeypatch DB_TYPE=sqlite），
避免打到真实 MySQL / 污染 backend/data/chain_logs.db。
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DB_TYPE", "sqlite")

from app.config import settings  # noqa: E402

# 让 store 走「同步 flush」路径（不等后台线程），断言才稳定
settings.chain_log_enabled = True
settings.chain_log_flush_interval = 3600  # 后台线程基本不抢活，测试用同步 flush()
settings.chain_log_flush_batch = 50
settings.chain_log_max_rows = 0
settings.chain_log_retention_days = 0
settings.chain_log_max_data_chars = 200


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """把 chainlog 的 sqlite 连接指向临时文件，并在用例结束后关停写入线程。"""
    import threading

    from app.chainlog import db as chain_db
    from app.chainlog import tracer

    monkeypatch.setattr(chain_db, "DB_PATH", tmp_path / "chain_logs.db")
    monkeypatch.setattr(chain_db, "_tls", threading.local())
    st = tracer.store()
    yield st
    st.stop(flush=True)
    chain_db.close_thread_conn()
    with tracer._seq_lock:
        tracer._seq_counters.clear()
    st._written = 0


# ── 上下文 ────────────────────────────────────────────────────────────────

def test_context_bind_nested_and_reset():
    from app import chainlog

    ctx = chainlog.new_context(trace_id="t-1", user_id="u1", path="/api/chat")
    assert chainlog.current() == {}
    with chainlog.bind(ctx) as bound:
        assert chainlog.trace_id() == "t-1"
        chainlog.set_fields(session_id="s1")
        inner = chainlog.new_context(trace_id="t-2")
        with chainlog.bind(inner):
            assert chainlog.trace_id() == "t-2"
        assert chainlog.trace_id() == "t-1"
    assert chainlog.current() == {}


def test_payload_attach_and_bind_from_payload():
    from app import chainlog

    ctx = chainlog.new_context(trace_id="t-9", session_id="s9", user_id="u9")
    with chainlog.bind(ctx):
        payload = chainlog.attach({"question": "hi"})
        assert payload[chainlog.TRACE_PAYLOAD_KEY]["trace_id"] == "t-9"
        # 跨 task（AgentBus）：原上下文已失效，仅靠 payload 也能恢复
        token = chainlog.bind_from_payload(payload)
        try:
            assert chainlog.trace_id() == "t-9"
            assert chainlog.payload_trace()["session_id"] == "s9"
        finally:
            chainlog.reset(token)
    # 无 trace 的 payload 不应产生上下文
    assert chainlog.bind_from_payload({"question": "x"}) is None
    assert chainlog.bind_from_payload(None) is None


# ── 写入 / 查询 ───────────────────────────────────────────────────────────

def test_write_flush_and_seq_ordering(tmp_db):
    from app import chainlog

    st = tmp_db
    ctx = chainlog.new_context(trace_id="t-seq", user_id="u1")
    with chainlog.bind(ctx):
        for i in range(5):
            chainlog.info("http", "test", f"n{i}", message=f"node {i}")
    st.flush()
    entries = st.list_entries(trace_id="t-seq", order="asc")["entries"]
    assert len(entries) == 5
    assert [e["seq"] for e in entries] == [1, 2, 3, 4, 5]
    assert entries[0]["user_id"] == "u1"


def test_span_records_start_end(tmp_db):
    from app import chainlog

    st = tmp_db
    with chainlog.bind(chainlog.new_context(trace_id="t-span")):
        with chainlog.span("llm", "test", "llm", data={"model": "m"}) as sp:
            sp["tokens"] = 42
            time.sleep(0.01)
    st.flush()
    entries = st.list_entries(trace_id="t-span", order="asc")["entries"]
    assert [e["event"] for e in entries] == ["llm.start", "llm.end"]
    # end 节点挂在 start 节点之下（span 父子串联），且拿到块内补写的字段
    assert entries[1]["parent_id"] == entries[0]["id"]
    assert entries[1]["data"]["tokens"] == 42
    assert entries[1]["duration_ms"] >= 5
    # start 节点是入队时的快照，不被块内改写污染
    assert "tokens" not in entries[0]["data"]


def test_span_records_error_and_reraises(tmp_db):
    from app import chainlog

    st = tmp_db
    with chainlog.bind(chainlog.new_context(trace_id="t-span-err")):
        with pytest.raises(ValueError):
            with chainlog.span("tool", "test", "tool.exec"):
                raise ValueError("tool blew up")
    st.flush()
    entries = st.list_entries(trace_id="t-span-err", order="asc")["entries"]
    assert [e["event"] for e in entries] == ["tool.exec.start", "tool.exec.error"]
    assert entries[1]["level"] == "ERROR"
    assert entries[1]["data"]["error"] == "tool blew up"
    assert entries[1]["data"]["error_type"] == "ValueError"


def test_query_filters_and_traces_aggregate(tmp_db):
    from app import chainlog

    st = tmp_db
    with chainlog.bind(chainlog.new_context(trace_id="t-a", user_id="u1")):
        chainlog.info("http", "chat", "chat.done", message="ok")
        chainlog.error("agent", "supervisor", "chat.error", message="boom")
    with chainlog.bind(chainlog.new_context(trace_id="t-b", user_id="u2")):
        chainlog.info("tool", "file_tools", "tool.done", message="read ok")
    st.flush()

    assert st.list_entries(level="ERROR")["total"] == 1
    assert st.list_entries(stage="tool")["total"] == 1
    assert st.list_entries(component="supervisor")["total"] == 1
    assert st.list_entries(keyword="boom")["total"] == 1

    traces = st.list_traces()["traces"]
    # 同毫秒写入的两条 trace 顺序必须确定（排序键带 trace_id 兜底）
    assert [t["trace_id"] for t in traces] == ["t-b", "t-a"]
    detail = st.trace_detail("t-a")
    assert [e["event"] for e in detail["entries"]] == ["chat.done", "chat.error"]
    assert detail["entries"][0]["user_id"] == "u1"

    only_err = st.list_traces(has_error=True)["traces"]
    assert [t["trace_id"] for t in only_err] == ["t-a"]

    stats = st.stats()
    assert stats["total"] == 3
    assert stats["traces"] == 2
    assert stats["by_level"]["ERROR"] == 1
    assert stats["by_stage"]["http"] == 1
    assert stats["top_errors"][0]["n"] == 1

    assert "supervisor" in st.distinct_values()["components"]
    assert "INFO" in st.distinct_values()["levels"]


def test_stats_counters_and_dropped(tmp_db):
    from app import chainlog

    st = tmp_db
    for i in range(3):
        chainlog.info("http", "test", f"e{i}")
    st.flush()
    counters = st.stats_counters()
    assert counters.get("written", 0) == 3
    assert counters.get("dropped", 0) == 0


def test_cleanup_max_rows_and_clear(tmp_db, monkeypatch):
    from app import chainlog

    st = tmp_db
    for i in range(5):
        chainlog.info("http", "test", f"e{i}")
    st.flush()
    assert st.list_entries()["total"] == 5

    # app.chainlog.store 这个名字在包命名空间里被同名函数遮蔽，必须走 sys.modules
    store_mod = sys.modules["app.chainlog.store"]
    monkeypatch.setattr(store_mod, "_max_rows", lambda: 2)
    result = st.cleanup()
    assert result["overflow"] == 3
    assert st.list_entries()["total"] == 2

    assert st.clear(trace_id="nope")["deleted"] == 0
    st.clear(all_rows=True)
    assert st.list_entries()["total"] == 0


def test_cleanup_respects_retention_days(tmp_db, monkeypatch):
    from app import chainlog

    st = tmp_db
    chainlog.info("http", "test", "old")
    st.flush()
    store_mod = sys.modules["app.chainlog.store"]
    monkeypatch.setattr(store_mod, "_retention_days", lambda: 1)
    # 把这行挪到 2 天前，验证 TTL 裁剪
    conn = st and sys.modules["app.chainlog.db"]._get_db()
    old = int(time.time() * 1000) - 2 * 86400 * 1000
    conn.execute("UPDATE chain_logs SET ts = ?", (old,))
    conn.commit()
    assert st.cleanup()["expired"] == 1
    assert st.list_entries()["total"] == 0


def test_oversized_data_keeps_valid_json(tmp_db, monkeypatch):
    """超长 data 不能被从中间截断 —— 否则结构化字段（answer_chars 等）全丢。"""
    from app import chainlog

    st = tmp_db
    store_mod = sys.modules["app.chainlog.store"]
    monkeypatch.setattr(store_mod, "_max_data_chars", lambda: 200)
    with chainlog.bind(chainlog.new_context(trace_id="t-big")):
        chainlog.info("agent", "test", "agent.done", data={
            "answer_chars": 123456,
            "preview": "长文本" * 500,
            "model": "deepseek/x",
        })
    st.flush()
    data = st.list_entries(trace_id="t-big")["entries"][0]["data"]
    # 关键标量必须保留，且没有退化成 _raw
    assert data["answer_chars"] == 123456
    assert data["model"] == "deepseek/x"
    assert data["_truncated"] is True
    assert len(data["preview"]) < len("长文本" * 500)


def test_log_never_raises(tmp_db, monkeypatch):
    """日志故障绝不能影响业务：store 抛异常时 log() 必须静默返回。"""
    from app import chainlog

    def boom(_entry):
        raise RuntimeError("db down")

    monkeypatch.setattr(chainlog.store(), "append", boom)
    chainlog.info("http", "test", "should.not.raise")  # 不抛即为通过


# ── 中间件 ────────────────────────────────────────────────────────────────

def _run_mw(method: str, url: str, headers=None, boom: bool = False):
    """用伪 ASGI 请求驱动中间件。

    返回 (下游观察到的 trace 上下文, 响应头 dict, 下游抛出的异常)。
    """
    import asyncio

    from app.chainlog import tracer
    from app.chainlog.middleware import ChainLogMiddleware

    seen: dict = {}
    sent: dict = {}

    async def downstream(scope, receive, send_):
        # 下游（endpoint）必须能读到中间件绑定的上下文
        seen.update(tracer.current())
        if boom:
            raise ValueError("downstream exploded")
        await send_({"type": "http.response.start", "status": 200, "headers": []})
        await send_({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http", "method": method, "path": url, "headers": headers or [],
        "client": ("127.0.0.1", 1234), "query_string": b"", "scheme": "http",
        "server": ("test", 80), "root_path": "", "http_version": "1.1",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
            sent["headers"] = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in message.get("headers") or []
            }

    err = None
    try:
        asyncio.run(ChainLogMiddleware(downstream)(scope, receive, send))
    except ValueError as exc:
        err = exc
    return seen, sent, err


def test_middleware_generates_and_propagates_trace_id(tmp_db):
    from app import chainlog

    st = tmp_db
    seen, sent, err = _run_mw("POST", "/api/chat/multi-agent")
    trace = seen.get("trace_id")
    assert trace and err is None
    assert sent["status"] == 200
    assert sent["headers"]["x-trace-id"] == trace
    assert seen["path"] == "/api/chat/multi-agent"
    assert seen["method"] == "POST"

    st.flush()
    entries = st.list_entries(trace_id=trace, order="asc")["entries"]
    # 收发两个节点必须落在同一条 trace（收尾节点不能脱离上下文另起 trace）
    assert [e["event"] for e in entries] == ["http.request", "http.response"]
    assert entries[1]["data"]["status"] == 200
    assert entries[1]["duration_ms"] is not None


def test_middleware_reuses_inbound_trace_id(tmp_db):
    seen, sent, _ = _run_mw(
        "GET", "/api/sessions", headers=[(b"x-trace-id", b"inbound-tid")],
    )
    assert seen["trace_id"] == "inbound-tid"
    assert sent["headers"]["x-trace-id"] == "inbound-tid"


def test_middleware_captures_user_id(tmp_db):
    seen, _, _ = _run_mw("GET", "/api/sessions", headers=[(b"x-user-id", b"alice")])
    assert seen["user_id"] == "alice"


def test_middleware_logs_error(tmp_db):
    from app import chainlog

    st = tmp_db
    _, _, err = _run_mw("GET", "/api/chat/x", boom=True)
    assert isinstance(err, ValueError)  # 异常必须原样上抛，不能被日志吞掉
    st.flush()
    errors = st.list_entries(level="ERROR", order="asc")["entries"]
    # 异常路径同时留下 http.error 与 http.response（500）两个 ERROR 节点，
    # 保证时间线闭合；两者必须同属一条 trace
    assert [e["event"] for e in errors] == ["http.error", "http.response"]
    assert errors[0]["data"]["error_type"] == "ValueError"
    assert errors[1]["trace_id"] == errors[0]["trace_id"]
    assert errors[1]["data"]["status"] == 500


def test_middleware_logs_client_error_as_warning(tmp_db):
    from app import chainlog

    st = tmp_db
    seen, _, _ = _run_mw("GET", "/api/chat/x")
    st.flush()
    resp = [
        e for e in st.list_entries(trace_id=seen["trace_id"])["entries"]
        if e["event"] == "http.response"
    ]
    assert resp and resp[0]["level"] == "INFO"


def test_middleware_skips_log_and_static_paths(tmp_db):
    from app import chainlog

    st = tmp_db
    for path in ("/api/logs", "/api/logs/stats", "/health", "/api/monitor/stats", "/docs"):
        seen, _, _ = _run_mw("GET", path)
        assert seen == {}  # 被跳过的路径不建立上下文
    st.flush()
    assert st.list_entries()["total"] == 0


def test_middleware_disabled_short_circuits(tmp_db, monkeypatch):
    from app import chainlog

    st = tmp_db
    monkeypatch.setattr(settings, "chain_log_enabled", False)
    seen, _, _ = _run_mw("GET", "/api/sessions")
    assert seen == {}
    st.flush()
    assert st.list_entries()["total"] == 0


# ── 事件镜像 ──────────────────────────────────────────────────────────────

def test_mirror_agent_lifecycle_events(tmp_db):
    from app import chainlog

    st = tmp_db
    with chainlog.bind(chainlog.new_context(trace_id="t-mirror-a", user_id="u1")):
        chainlog.mirror_event(
            {"type": "agent_start", "agent_id": "build", "agent_name": "Build"}
        )
        chainlog.mirror_event(
            {"type": "agent_done", "agent_id": "build", "content": "答案" * 300}
        )
        chainlog.mirror_event(
            {"type": "agent_error", "agent_id": "build", "error": "kaboom"}
        )
    st.flush()
    entries = st.list_entries(trace_id="t-mirror-a", order="asc")["entries"]
    assert [e["event"] for e in entries] == ["agent.start", "agent.done", "agent.error"]
    assert all(e["agent_id"] == "build" for e in entries)
    assert [e["stage"] for e in entries] == ["agent", "agent", "agent"]
    assert entries[1]["data"]["answer_chars"] == 600
    assert entries[2]["level"] == "ERROR"
    assert entries[2]["data"]["error"] == "kaboom"


def test_mirror_tool_steps_and_permission(tmp_db):
    """步骤事件用真实的 AgentEventCollector 载荷形状（type/step_id/name/status）。"""
    from app import chainlog

    st = tmp_db
    with chainlog.bind(chainlog.new_context(trace_id="t-mirror-t")):
        chainlog.mirror_event({
            "type": "agent_step", "agent_id": "build",
            "step": {
                "type": "tool_end", "step_id": "tool_tool_read_file",
                "name": "调用工具: tool_read_file", "status": "completed",
                "tool_name": "tool_read_file", "tool_args": {"path": "a.py"},
                "duration_ms": 12,
            },
        })
        chainlog.mirror_event({
            "type": "agent_step", "agent_id": "build",
            "step": {
                "type": "tool_start", "step_id": "tool_tool_execute",
                "name": "调用工具: tool_execute", "status": "running",
                "tool_name": "tool_execute",
            },
        })
        chainlog.mirror_event({
            "type": "agent_step", "agent_id": "build",
            "step": {
                "type": "step_end", "step_id": "generate",
                "name": "生成回答", "status": "error", "detail": "内容被过滤",
                "duration_ms": 30,
            },
        })
        chainlog.mirror_event({
            "type": "permission_request", "agent_id": "build", "request_id": "p1",
            "operation": "write", "path": "/tmp/a.txt", "tool_name": "tool_write_file",
        })
        # 非步骤事件（token 增量 / 工具原始输出）不产生链路节点，避免噪音
        chainlog.mirror_event({"type": "text_delta", "delta": "hi"})
        chainlog.mirror_event({"type": "tool_output", "agent_id": "build", "line": "x"})
    st.flush()
    entries = st.list_entries(trace_id="t-mirror-t", order="asc")["entries"]
    assert [e["event"] for e in entries] == [
        "调用工具: tool_read_file", "调用工具: tool_execute",
        "生成回答", "permission.request",
    ]
    # tool_* 归 tool 阶段，step_* 归 agent 阶段，审批独立阶段
    assert [e["stage"] for e in entries] == ["tool", "tool", "agent", "permission"]
    assert all(e["agent_id"] == "build" for e in entries)
    assert entries[0]["duration_ms"] == 12
    assert entries[0]["data"]["tool_name"] == "tool_read_file"
    assert entries[0]["data"]["tool_args"] == {"path": "a.py"}
    # generate 步骤 status=error → ERROR 级
    assert entries[2]["level"] == "ERROR"
    assert entries[2]["data"]["detail"] == "内容被过滤"
    assert entries[3]["data"]["path"] == "/tmp/a.txt"
    assert entries[3]["data"]["request_id"] == "p1"


def test_mirror_tool_steps_can_be_disabled(tmp_db, monkeypatch):
    from app import chainlog

    st = tmp_db
    monkeypatch.setattr(settings, "chain_log_tool_calls", False)
    with chainlog.bind(chainlog.new_context(trace_id="t-mirror-off")):
        chainlog.mirror_event({
            "type": "agent_step", "agent_id": "build",
            "step": {"type": "tool_end", "step_id": "tool_tool_ls",
                     "name": "调用工具: tool_ls", "status": "completed",
                     "tool_name": "tool_ls"},
        })
        chainlog.mirror_event({"type": "agent_done", "agent_id": "build", "content": "x"})
    st.flush()
    events = [e["event"] for e in st.list_entries(trace_id="t-mirror-off")["entries"]]
    assert events == ["agent.done"]


# ── HTTP API ──────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_db, monkeypatch):
    """FastAPI TestClient：只挂载 logs router，避免启动整个后端运行时。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.logs import router

    monkeypatch.setattr(settings, "admin_token", "")
    app = FastAPI()
    app.include_router(router)
    # client=(127.0.0.1) 让 require_admin 判定为「本机 + 未配 ADMIN_TOKEN = 管理员」
    with TestClient(app, client=("127.0.0.1", 54321)) as c:
        yield c


def _seed(trace_id: str, **kw) -> str:
    from app import chainlog

    st = chainlog.store()
    with chainlog.bind(chainlog.new_context(trace_id=trace_id, **kw)):
        chainlog.info("http", "chat", "chat.done", message="done")
        chainlog.error("agent", "supervisor", "chat.error", message="bad")
    st.flush()
    return trace_id


def test_api_list_and_detail(client):
    tid = _seed("api-1", user_id="anonymous")
    r = client.get("/api/logs", params={"view": "traces"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["traces"][0]["trace_id"] == tid

    r = client.get("/api/logs", params={"view": "entries", "level": "ERROR"})
    assert r.json()["data"]["total"] == 1

    r = client.get(f"/api/logs/traces/{tid}")
    assert r.json()["data"]["trace_id"] == tid
    assert len(r.json()["data"]["entries"]) == 2

    r = client.get("/api/logs/stats")
    assert r.json()["data"]["traces"] == 1

    r = client.get("/api/logs/filters")
    assert "supervisor" in r.json()["data"]["components"]


def test_api_admin_only_write_endpoints(client):
    # 本机（client=127.0.0.1）+ 未配 ADMIN_TOKEN → 视为管理员
    assert client.post("/api/logs/cleanup").status_code == 200
    tid = _seed("api-2", user_id="anonymous")
    r = client.post("/api/logs/clear", json={"trace_id": tid})
    assert r.json()["data"]["deleted"] == 2
    assert client.get("/api/logs").json()["data"]["total"] == 0


def test_api_write_requires_admin_when_token_set(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    assert client.post("/api/logs/cleanup").status_code == 401
    r = client.post(
        "/api/logs/clear", json={}, headers={"Authorization": "Bearer secret"},    )
    assert r.status_code == 200


@pytest.fixture()
def user_client(tmp_db, monkeypatch):
    """非管理员客户端：远端 host（不在 127.0.0.1）+ 未配 ADMIN_TOKEN → 走 user_id 隔离。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.logs import router

    monkeypatch.setattr(settings, "admin_token", "")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, client=("10.0.0.9", 54321)) as c:
        yield c


def _own() -> dict:
    return {"headers": {"X-User-Id": "alice"}}


def test_api_isolates_users_in_sql(user_client):
    """非管理员只看得到自己的日志 —— 隔离必须下推到 SQL。

    若在 Python 侧分页后过滤：total / stats.traces 会按全库算，
    非管理员既能翻到别人的链路，也能通过计数推断别人的活动量。
    """
    _seed("iso-alice", user_id="alice")
    _seed("iso-bob", user_id="bob")
    # 系统级日志（user_id 为空）对所有人可见
    from app import chainlog

    st = chainlog.store()
    with chainlog.bind(chainlog.new_context(trace_id="iso-sys", user_id="")):
        chainlog.info("system", "main", "boot.done", message="started")
    st.flush()

    traces = user_client.get("/api/logs", params={"view": "traces"}, **_own()).json()["data"]
    ids = {t["trace_id"] for t in traces["traces"]}
    assert ids == {"iso-alice", "iso-sys"}
    # total 走 SQL 计数，不能是全库的 3
    assert traces["total"] == 2

    entries = user_client.get(
        "/api/logs", params={"view": "entries", "offset": 0, "limit": 50}, **_own(),
    ).json()["data"]
    assert {e["trace_id"] for e in entries["entries"]} == {"iso-alice", "iso-sys"}
    assert entries["total"] == 3  # alice 2 条 + 系统 1 条

    # 别人的链路下钻为空
    detail = user_client.get("/api/logs/traces/iso-bob", **_own()).json()["data"]
    assert detail["entries"] == []

    # 聚合统计同样只算自己的
    stats = user_client.get("/api/logs/stats", **_own()).json()["data"]
    assert stats["traces"] == 2
    assert stats["total"] == 3
    assert {t["trace_id"] for t in stats["slow_traces"]} <= {"iso-alice", "iso-sys"}

    # 筛选下拉不泄漏别人的组件/会话
    opts = user_client.get("/api/logs/filters", **_own()).json()["data"]
    assert "supervisor" in opts["components"]
    assert "iso-bob" not in opts["sessions"]
