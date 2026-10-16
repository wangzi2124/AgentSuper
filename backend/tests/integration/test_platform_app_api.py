"""L3 集成测试：平台 FastAPI 层（agent.md §9 app/）。

用 Starlette TestClient + 假模型，验证 /chat、/chat/stream、/sessions、/admin。
"""

import json

import pytest
from fastapi.testclient import TestClient

from execution.filesystem import VirtualFileSystem
from models.router import ChatResult
from app.main import create_app


class FakeModel:
    """始终返回固定答案的假模型（TierModel 契约）。"""

    def __init__(self, content="最终答案"):
        self.tier = "fake"
        self._content = content

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content=self._content, finish_reason="stop")


@pytest.fixture
def client(tmp_path):
    app = create_app(model=FakeModel(), vfs=VirtualFileSystem(root=str(tmp_path)))
    with TestClient(app) as c:
        yield c


def test_root_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_admin_health_initialized(client):
    body = client.get("/api/admin/health").json()
    assert body["status"] == "ok"
    assert "research" in body["agents"]


def test_admin_tools_and_agents(client):
    tools = client.get("/api/admin/tools").json()
    assert tools["count"] > 0
    names = {t["name"] for t in tools["tools"]}
    assert {"read_file", "write_file"}.issubset(names)
    agents = client.get("/api/admin/agents").json()["agents"]
    assert {a["name"] for a in agents} == {"research", "code", "file"}


def test_admin_prompts_and_metrics(client):
    prompts = client.get("/api/admin/prompts").json()
    assert prompts["count"] > 0
    metrics = client.get("/api/admin/metrics").json()
    assert "total_calls" in metrics


def test_chat_non_stream(client):
    resp = client.post("/api/chat", json={"message": "1+1=?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"]
    assert body["final_output"] == "最终答案"


def test_chat_stream_emits_events(client):
    with client.stream("POST", "/api/chat/stream", json={"message": "hi"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "event: start" in text
    assert "event: node" in text
    assert "event: done" in text
    assert "最终答案" in text


def test_session_lifecycle(client):
    created = client.post("/api/sessions", json={"title": "t"}).json()
    sid = created["id"]
    # 前端形状：裸 list[SessionInfo]
    listed = client.get("/api/sessions").json()
    assert isinstance(listed, list) and listed
    assert listed[0]["kind"] == "multi-agent"
    assert "time_created" in created
    assert client.get(f"/api/sessions/{sid}").json()["id"] == sid
    # 跑一轮后消息可见
    client.post("/api/chat", json={"message": "hi", "session_id": sid})
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert isinstance(msgs, list) and msgs
    assert msgs[0]["role"] == "user"
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_session_patch_status_and_prompts(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    patched = client.patch(f"/api/sessions/{sid}", json={"title": "renamed"}).json()
    assert patched["title"] == "renamed"
    assert client.get(f"/api/sessions/{sid}/status").json()["status"] == "idle"
    # 待处理任务队列
    enq = client.post(
        f"/api/sessions/{sid}/prompts",
        json={"prompt": {"message": "后续任务"}, "client_msg_id": "c1"},
    ).json()
    assert enq["id"] == "c1"
    assert client.get(f"/api/sessions/{sid}/prompts").json()[0]["message"] == "后续任务"
    assert client.delete(f"/api/sessions/{sid}/prompts/c1").json()["removed"] is True
    assert client.post(f"/api/sessions/{sid}/interrupt").status_code == 204


def test_session_messages_on_chat_creates_session(client):
    body = client.post("/api/chat", json={"message": "hello"}).json()
    sid = body["session_id"]
    assert client.get(f"/api/sessions/{sid}/state").status_code == 200


def test_missing_session_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/messages").status_code == 404


def test_multi_agent_non_stream_envelope(client):
    body = client.post("/api/chat/multi-agent", json={"message": "hi"}).json()
    assert body["code"] == 0
    assert body["data"]["answer"] == "最终答案"
    assert body["data"]["conversation_id"]


def test_multi_agent_stream_frontend_protocol(client):
    with client.stream("POST", "/api/chat/multi-agent/stream", json={"message": "hi"}) as r:
        assert r.status_code == 200
        assert r.headers.get("X-Session-Id")
        text = "".join(r.iter_text())
    types = [
        json.loads(line[6:])["type"]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    assert types[0] == "routing"
    assert "agent_start" in types
    assert types[-1] == "done"
    assert "data: " in text and '"answer"' in text



def test_approvals_empty_and_decision_404(client):
    assert client.get("/api/admin/approvals").json()["approvals"] == []
    assert (
        client.post("/api/admin/approvals/missing", json={"approved": True}).status_code
        == 404
    )


# ── 轮次快照：restore-snapshot / diff（对齐前端契约）─────────────────────────

def test_snapshot_module_add_modify_delete(tmp_path):
    from app.snapshot import (
        render_diff,
        restore,
        snapshot_begin,
        snapshot_end,
    )

    vfs = VirtualFileSystem(root=str(tmp_path))
    (tmp_path / "keep.txt").write_text("old\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("bye\n", encoding="utf-8")

    begin = snapshot_begin(vfs)
    vfs.write_file("new.txt", "hi\n")            # added
    vfs.write_file("keep.txt", "new\n")          # modified
    from execution.tools.file_tools import delete_file

    delete_file(vfs, "gone.txt")                 # deleted（经 VFS 历史，可回滚）
    files_changed, descriptor = snapshot_end(vfs, begin)

    by_file = {f["file"]: f for f in files_changed}
    assert by_file["new.txt"]["status"] == "added"
    assert by_file["new.txt"]["additions"] == 1
    assert by_file["keep.txt"]["status"] == "modified"
    assert by_file["gone.txt"]["status"] == "deleted"

    # diff 文本包含改动路径
    diff = render_diff(vfs, descriptor)
    assert {f["file"] for f in diff["files"]} == {"new.txt", "keep.txt", "gone.txt"}

    # restore → new.txt 删除、keep.txt 还原、gone.txt 恢复
    result = restore(vfs, descriptor)
    assert result["restored"] is True
    assert not (tmp_path / "new.txt").exists()
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "gone.txt").read_text(encoding="utf-8") == "bye\n"


def test_restore_snapshot_and_diff_endpoints(client, tmp_path):
    from app.snapshot import snapshot_begin, snapshot_end

    ctx = client.app.state.platform
    begin = snapshot_begin(ctx.vfs)
    ctx.vfs.write_file("a.txt", "hello\n")
    files_changed, descriptor = snapshot_end(ctx.vfs, begin)
    ctx.snapshots["am_x"] = descriptor
    assert files_changed[0]["file"] == "a.txt"
    assert files_changed[0]["status"] == "added"

    # diff
    diff = client.post(
        "/api/chat/multi-agent/diff",
        json={"conversation_id": "s", "message_id": "am_x"},
    ).json()
    assert diff["files"][0]["file"] == "a.txt"
    assert "+hello" in diff["files"][0]["diff"]

    # restore → 文件回到「本轮之前不存在」
    res = client.post(
        "/api/chat/multi-agent/restore-snapshot",
        json={"conversation_id": "s", "message_id": "am_x"},
    ).json()
    assert res["restored"] is True
    assert not (tmp_path / "a.txt").exists()


def test_restore_and_diff_without_snapshot(client):
    res = client.post(
        "/api/chat/multi-agent/restore-snapshot", json={"message_id": "missing"}
    ).json()
    assert res["restored"] is False and res["already"] is True
    diff = client.post("/api/chat/multi-agent/diff", json={"message_id": "missing"}).json()
    assert diff["files"] == []


# ── token / 成本真值（graph.usage + SSE done 事件）────────────────────────────

def test_usage_merge_and_delta():
    from graph.usage import merge_usage, usage_delta

    merged = merge_usage({"input": 1, "output": 2, "cost": 0.1}, {"input": 3, "cost": 0.2})
    assert merged["input"] == 4 and merged["output"] == 2 and merged["cost"] == pytest.approx(0.3)

    delta = usage_delta(
        {"input": 10, "output": 5, "cost": 0.5},
        {"input": 18, "output": 9, "cost": 1.1},
    )
    assert delta["input"] == 8 and delta["output"] == 4 and delta["cost"] == pytest.approx(0.6)


def test_estimate_cost_prefers_catalog():
    from observability.cost import estimate_cost

    # 目录单价：input 0.27 / output 1.1（每 1M tokens）
    cost = estimate_cost("deepseek/deepseek-v4-flash", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.27 + 1.1)


def test_usage_from_result_computes_cost(monkeypatch):
    from graph import usage as usage_mod

    monkeypatch.setattr(usage_mod, "estimate_cost", lambda m, pt, ct: pt * 1e-6 + ct * 2e-6)
    result = ChatResult(content="x", model="fake-model", usage={"input": 12, "output": 7, "total": 19})
    out = usage_mod.usage_from_result(result)
    assert out["input"] == 12 and out["output"] == 7 and out["total"] == 19
    assert out["calls"] == 1
    assert out["cost"] == pytest.approx(12e-6 + 14e-6)


class UsageFakeModel:
    """带 usage 的假模型，用于验证 done 事件的 token/cost 真值。"""

    def __init__(self):
        self.tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(
            content="用量答案",
            finish_reason="stop",
            model="fake-model",
            usage={"input": 12, "output": 7, "total": 19},
        )


@pytest.fixture
def usage_client(tmp_path, monkeypatch):
    from graph import usage as usage_mod

    monkeypatch.setattr(usage_mod, "estimate_cost", lambda m, pt, ct: pt * 1e-6 + ct * 2e-6)
    app = create_app(model=UsageFakeModel(), vfs=VirtualFileSystem(root=str(tmp_path)))
    with TestClient(app) as c:
        yield c


def _done_event(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event.get("type") == "done":
                return event
    raise AssertionError("no done event")


def test_stream_done_reports_real_tokens_and_cost(usage_client):
    with usage_client.stream(
        "POST", "/api/chat/multi-agent/stream", json={"message": "hi"}
    ) as r:
        text = "".join(r.iter_text())
    done = _done_event(text)
    assert done["tokens"]["input"] == 12
    assert done["tokens"]["output"] == 7
    assert done["cost"] == pytest.approx(12e-6 + 14e-6)

    # 会话聚合列同步累加
    sessions = usage_client.get("/api/sessions").json()
    sess = sessions[0]
    assert sess["tokens_input"] == 12
    assert sess["tokens_output"] == 7
    assert sess["cost"] == pytest.approx(12e-6 + 14e-6)


def test_second_turn_reports_only_turn_delta(usage_client):
    # 第一轮
    with usage_client.stream(
        "POST", "/api/chat/multi-agent/stream", json={"message": "hi"}
    ) as r:
        first = _done_event("".join(r.iter_text()))
    sid = first["conversation_id"]
    # 第二轮（同一会话）：done 只报本轮增量，而非累计
    with usage_client.stream(
        "POST", "/api/chat/multi-agent/stream", json={"message": "again", "conversation_id": sid}
    ) as r:
        second = _done_event("".join(r.iter_text()))
    assert second["tokens"]["input"] == 12 and second["tokens"]["output"] == 7
    assert second["cost"] == pytest.approx(12e-6 + 14e-6)


def test_non_stream_reports_tokens_and_cost(usage_client):
    body = usage_client.post("/api/chat/multi-agent", json={"message": "hi"}).json()
    assert body["data"]["tokens"]["input"] == 12
    assert body["data"]["tokens"]["output"] == 7
    assert body["data"]["cost"] == pytest.approx(12e-6 + 14e-6)


# ── 鉴权中间件 + 账号注册/登录（登录注册接口对接）─────────────────────────────

def test_account_register_login_token_roundtrip(tmp_path, monkeypatch):
    import app.auth as auth
    from config import settings

    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_users_path", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "_users_cache", None)

    ok, err, uid = auth.register_account("tester1", "pass1234")
    assert ok, err
    assert auth.authenticate_account("tester1", "pass1234") == uid
    assert auth.authenticate_account("tester1", "wrong") == ""
    assert auth.register_account("tester1", "pass1234")[0] is False  # 重名

    token, _ = auth.issue_token(uid)
    assert auth.verify_token(uid, token) is True
    assert auth.verify_token("u-other", token) is False  # sub 不匹配
    assert auth.verify_token(uid, token[:-1] + ("0" if token[-1] != "0" else "1")) is False


def test_auth_middleware_enforced_on_api(tmp_path, monkeypatch):
    from app.auth import issue_token
    from config import settings

    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    app = create_app(
        model=FakeModel(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_middleware=True,
    )
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # 非 /api → 放行
        # 缺 token → 401 信封
        denied = c.post("/api/chat/multi-agent", json={"message": "hi"})
        assert denied.status_code == 401
        assert denied.json()["code"] == 401
        # /api/auth/* 豁免（轻量 app 未挂 auth 路由 → 404 而非 401）
        assert c.get("/api/auth/status").status_code != 401
        # GET /api/voice/status 只读探测豁免 → 非 401
        assert c.get("/api/voice/status").status_code != 401
        # 合法 token → 放行
        token, _ = issue_token("u-test")
        hdr = {"X-User-Id": "u-test", "X-Auth-Token": token}
        assert (
            c.post("/api/chat/multi-agent", json={"message": "hi"}, headers=hdr).status_code
            == 200
        )
        # uid 与 token 不匹配 → 401
        bad = {"X-User-Id": "u-other", "X-Auth-Token": token}
        assert (
            c.post("/api/chat/multi-agent", json={"message": "hi"}, headers=bad).status_code
            == 401
        )


def test_auth_disabled_when_no_secret(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "auth_secret", "")
    app = create_app(
        model=FakeModel(),
        vfs=VirtualFileSystem(root=str(tmp_path)),
        enable_middleware=True,
    )
    with TestClient(app) as c:
        # 未配置 secret → 全放行
        assert c.post("/api/chat/multi-agent", json={"message": "hi"}).status_code == 200


# ── 会话端点真行为：历史回放 / revert / delete / compact / fork / children ─────


def _sid_after_turn(client, message="hi", **extra):
    body = client.post(
        "/api/chat/multi-agent", json={"message": message, **extra}
    ).json()
    return body["data"]["conversation_id"]


def _stream_events(client, payload):
    with client.stream("POST", "/api/chat/multi-agent/stream", json=payload) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_history_replays_user_and_assistant(client):
    sid = _sid_after_turn(client)
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["content"] == "最终答案"
    assert [m["seq"] for m in msgs] == [1, 2]


def test_revert_deletes_target_and_after(client):
    sid = _sid_after_turn(client)
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    # 撤回 assistant 回复（含自身）→ 仅删除该条，保留 user
    body = client.post(
        f"/api/sessions/{sid}/revert", json={"message_id": msgs[1]["id"]}
    ).json()
    assert body["deleted"] == 1
    assert [m["id"] for m in body["messages"]] == [msgs[0]["id"]]
    # 撤回至首条 → 连同其之后全部删除
    body2 = client.post(
        f"/api/sessions/{sid}/revert", json={"message_id": msgs[0]["id"]}
    ).json()
    assert body2["deleted"] == 1
    assert body2["messages"] == []


def test_delete_message_hides_single(client):
    sid = _sid_after_turn(client)
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert client.delete(f"/api/sessions/{sid}/messages/{msgs[0]['id']}").status_code == 204
    after = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["id"] for m in after] == [msgs[1]["id"]]


def test_compact_collapses_history(client):
    sid = _sid_after_turn(client)
    _sid_after_turn(client, "again", conversation_id=sid)  # → 4 条可见消息
    ctx = client.app.state.platform
    assert len(client.get(f"/api/sessions/{sid}/messages").json()) == 4

    # 端点（默认 keep_last=10 → 无操作，仅 204）
    assert client.post(f"/api/sessions/{sid}/compact").status_code == 204

    # 单元级 keep_last=1 → 折叠为「摘要占位 + 最近 1 条」
    import asyncio

    result = asyncio.run(ctx.compact_session(sid, keep_last=1))
    assert result["compacted"] is True and result["baseline"] == 3
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert msgs[0]["id"].endswith("_compact")
    assert msgs[0]["type"] == "system"
    assert [m["seq"] for m in msgs[1:]] == [4]


def test_fork_copies_visible_and_lists_children(client):
    sid = _sid_after_turn(client)
    parent_msgs = client.get(f"/api/sessions/{sid}/messages").json()

    forked = client.post(f"/api/sessions/{sid}/fork").json()
    assert forked["parent_id"] == sid
    assert forked["title"].endswith("(分支)")
    child_msgs = client.get(f"/api/sessions/{forked['id']}/messages").json()
    assert [m["role"] for m in child_msgs] == [m["role"] for m in parent_msgs]
    assert [m["content"] for m in child_msgs] == [m["content"] for m in parent_msgs]

    children = client.get(f"/api/sessions/{sid}/children").json()
    assert [c["id"] for c in children] == [forked["id"]]


def test_fork_with_message_id_truncates(client):
    sid = _sid_after_turn(client)
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    forked = client.post(
        f"/api/sessions/{sid}/fork", params={"message_id": msgs[0]["id"]}
    ).json()
    child_msgs = client.get(f"/api/sessions/{forked['id']}/messages").json()
    assert [m["role"] for m in child_msgs] == ["user"]


def test_interrupt_cancels_in_flight_turn(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    ctx = client.app.state.platform
    event = ctx.begin_turn(sid)
    assert client.post(f"/api/sessions/{sid}/interrupt").status_code == 204
    assert event.is_set() is True


# ── SSE 新事件：model_switched / queued / queue_drain / turn_done ─────────────


def test_stream_emits_model_switched_first(client):
    events = _stream_events(
        client, {"message": "hi", "model": "deepseek/deepseek-v4-flash"}
    )
    assert events[0]["type"] == "model_switched"
    assert events[0]["model_ref"]["id"] == "deepseek/deepseek-v4-flash"
    assert events[-1]["type"] == "done"


def test_stream_turn_done_drains_queued_prompt(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/prompts",
        json={"prompt": {"message": "排队任务"}, "client_msg_id": "q1"},
    )
    events = _stream_events(client, {"message": "hi", "conversation_id": sid})
    types = [e["type"] for e in events]
    assert "queue_drain" in types
    assert "turn_done" in types
    assert types[-1] == "done"
    assert types.index("turn_done") < types.index("done")
    turn_done = next(e for e in events if e["type"] == "turn_done")
    assert turn_done["queued_turn"] is True
    assert turn_done["next_prompt_id"] == "q1"
    done = events[-1]
    assert done["queued_turn"] is False
    # 排队任务也执行了 → 4 条可见消息
    msgs = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2]["content"] == "排队任务"


class _LockedOnceSem:
    """首帧即「无空闲槽」的假信号量：触发 queued 分支但不真正阻塞。"""

    def __init__(self):
        self.entered = False

    def locked(self):
        return not self.entered

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        return False


def test_stream_queued_when_all_slots_busy(client):
    ctx = client.app.state.platform
    ctx.semaphore = _LockedOnceSem()
    events = _stream_events(client, {"message": "hi"})
    assert events[0]["type"] == "queued"
    assert events[0]["queue_position"] == 1
    assert events[-1]["type"] == "done"
    assert ctx.queue_counter == 0  # 出队后计数回落


# ── 审批：ApprovalQueue.await_decision + run_tool_loop 钩子 + request_permission ─


def test_approval_queue_await_decision():
    import asyncio

    from execution.approval import ApprovalQueue

    async def scenario():
        q = ApprovalQueue()
        rid = await q.request("danger", {"path": "x"})

        async def decide_later():
            await asyncio.sleep(0.01)
            await q.decide(rid, True, reason="ok")

        import asyncio as _a

        _a.create_task(decide_later())
        approved = await q.await_decision(rid)
        # 超时路径
        rid2 = await q.request("danger", {"path": "y"})
        expired = await q.await_decision(rid2, timeout=0.01)
        return approved, expired

    approved, expired = asyncio.run(scenario())
    assert approved == "approved"
    assert expired == "expired"


class _ScriptedModel:
    """首轮发起 tool_call，次轮返回最终答案（模拟审批后继续）。"""

    def __init__(self, tool="danger"):
        self.tier = "fake"
        self._tool = tool
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        from models.router import ToolCall

        self.calls += 1
        if self.calls == 1:
            return ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name=self._tool, arguments="{}")],
                finish_reason="tool_calls",
            )
        return ChatResult(content="完成", finish_reason="stop")


def test_run_tool_loop_permission_hook_denies_and_allows():
    import asyncio

    from execution.tool_registry import ToolDefinition, ToolRegistry
    from graph.nodes.base import reset_permission_hook, run_tool_loop, set_permission_hook

    executed = {"n": 0}

    def danger() -> str:
        executed["n"] += 1
        return "已执行"

    def build_registry():
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="danger",
                description="高危工具",
                fn=danger,
                scope="global",
                require_approval=True,
            )
        )
        return reg

    async def run(decision):
        calls = []

        async def hook(tool, args):
            calls.append(tool)
            return decision

        token = set_permission_hook(hook)
        try:
            return await run_tool_loop(_ScriptedModel(), [{"role": "user", "content": "go"}], build_registry(), scope="global"), calls
        finally:
            reset_permission_hook(token)

    denied, calls = asyncio.run(run(False))
    assert calls == ["danger"]
    assert denied["tool_trace"][0]["output"].startswith("已拒绝执行工具 danger")
    assert executed["n"] == 0

    allowed, _ = asyncio.run(run(True))
    assert allowed["tool_trace"][0]["output"] == "已执行"
    assert executed["n"] == 1


def test_request_permission_hook_override_emits_and_decides(client):
    import asyncio

    ctx = client.app.state.platform
    captured = []

    async def hook(*, session_id, tool, args, emit):
        await emit({"type": "permission_request", "request_id": "r1", "tool_name": tool})
        captured.append((session_id, tool))
        return False

    ctx.permission_hook = hook
    try:
        emitted = []

        async def _emit(event):
            emitted.append(event)

        allowed = asyncio.run(
            ctx.request_permission(session_id="s1", tool="danger", args={}, emit=_emit)
        )
    finally:
        ctx.permission_hook = None
    assert allowed is False
    assert captured == [("s1", "danger")]
    assert emitted == [{"type": "permission_request", "request_id": "r1", "tool_name": "danger"}]


def test_request_permission_auto_allows_in_sandbox(client, tmp_path_factory):
    """沙箱内 / 允许根内路径免审批（对齐 external_directory ask：仅越界才询问）。"""
    import asyncio
    from pathlib import Path

    from execution.filesystem import reset_allowed_roots, set_allowed_roots

    ctx = client.app.state.platform

    async def _emit(event):
        return None

    # 沙箱默认根（vfs.root）内 → 直接放行，不触发 PermissionManager
    inside = str(ctx.vfs.root / "sub" / "x.txt")
    assert (
        asyncio.run(
            ctx.request_permission(
                session_id="s", tool="write_file", args={"path": inside}, emit=_emit
            )
        )
        is True
    )

    # 额外允许根（工作目录面板）内 → 直接放行（该目录在沙箱之外）
    ext = Path(tmp_path_factory.mktemp("vfs_ext"))
    assert ctx.vfs.is_allowed(str(ext / "y.txt")) is False
    token = set_allowed_roots([str(ext)])
    try:
        assert ctx.vfs.is_allowed(str(ext / "y.txt")) is True
        ctx.vfs.write_file(str(ext / "y.txt"), "ok")
        assert (ext / "y.txt").read_text(encoding="utf-8") == "ok"
        assert (
            asyncio.run(
                ctx.request_permission(
                    session_id="s",
                    tool="write_file",
                    args={"path": str(ext / "y.txt")},
                    emit=_emit,
                )
            )
            is True
        )
    finally:
        reset_allowed_roots(token)
    assert ctx.vfs.is_allowed(str(ext / "y.txt")) is False


