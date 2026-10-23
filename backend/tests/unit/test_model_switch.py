"""L1：模型切换上下文迁移（agent-3 §2.2/§2.3）。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

import app.deps as deps
from app.deps import ModelSwitchError, PlatformContext, initial_state


class _FakeSessions:
    def __init__(self, sess):
        self.sess = sess
        self.persisted: list[str] = []

    def get(self, session_id):
        return self.sess

    def persist(self, session_id):
        self.persisted.append(session_id)


class _FakeCtx:
    def __init__(self, sess, messages, summary=""):
        self.sessions = _FakeSessions(sess)
        self.cancels: dict = {}
        self._messages = messages
        self._summary = summary

    async def _raw_messages(self, session_id):
        return self._messages

    async def state(self, session_id):
        return {}

    def context_binding(self, session_id):
        return {"summary": self._summary, "tail_start_id": None}


def _sess(model="old/model", status="idle"):
    return SimpleNamespace(model=model, status=status, updated_at=0.0)


async def _switch(ctx, target):
    return await PlatformContext.switch_model(ctx, "ses_1", target)


def _audit_events(tmp_path):
    import json

    path = tmp_path / "backend" / "data" / "model_switches" / "switches.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def test_switch_rejects_unknown_window(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 0})
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))
    sess = _sess()
    ctx = _FakeCtx(sess, [])
    with pytest.raises(ModelSwitchError) as exc:
        await _switch(ctx, "new/model")
    assert exc.value.status_code == 400
    assert sess.model == "old/model"
    events = _audit_events(tmp_path)
    assert events[-1]["status"] == "rejected"
    assert "窗口未知" in events[-1]["error"]


async def test_switch_rejects_running_session(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 128000})
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))
    sess = _sess(status="running")
    ctx = _FakeCtx(sess, [])
    with pytest.raises(ModelSwitchError) as exc:
        await _switch(ctx, "new/model")
    assert exc.value.status_code == 409
    assert sess.model == "old/model"
    events = _audit_events(tmp_path)
    assert events[-1]["status"] == "rejected"
    assert "正在运行" in events[-1]["error"]


async def test_switch_same_model_is_noop(monkeypatch):
    called = False

    async def _compress(*_a, **_k):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("cognition.compressor.compress_context", _compress)
    sess = _sess()
    out = await _switch(_FakeCtx(sess, []), "old/model")
    assert out["switched"] is False
    assert called is False


async def test_switch_below_threshold_updates_model_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 128000})
    monkeypatch.setattr("cognition.compressor.estimate_tokens", lambda _text: 1)
    upserts: list[tuple] = []
    monkeypatch.setattr("app.session.repository.get_epoch", lambda _sid: None)
    monkeypatch.setattr("app.session.repository.latest_seq", lambda _sid: 0)
    monkeypatch.setattr(
        "app.session.repository.upsert_epoch",
        lambda sid, baseline, seq, snapshot: upserts.append((sid, baseline, seq, snapshot)),
    )
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))
    sess = _sess()
    ctx = _FakeCtx(sess, [HumanMessage(content="hi", id="m1")])
    out = await _switch(ctx, "new/model")
    assert out["switched"] is True
    assert out["summarized"] is False
    assert sess.model == "new/model"
    assert ctx.sessions.persisted == ["ses_1"]
    assert upserts and upserts[0][3]["context_window"] == 128000
    assert upserts[0][3]["compress_threshold"] == 76800
    assert list((tmp_path / "backend" / "data" / "model_switches").glob("*.json"))
    events = _audit_events(tmp_path)
    assert events[-1]["status"] == "committed"
    assert events[-1]["summarized"] is False


async def test_switch_above_threshold_writes_summary_and_tail(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 10000})
    monkeypatch.setattr("cognition.compressor.estimate_tokens", lambda _text: 10_000)
    m1 = HumanMessage(content="old", id="m1")
    m2 = HumanMessage(content="recent", id="m2")
    calls: list[dict] = []

    async def _compress(messages, **kwargs):
        calls.append(kwargs)
        return {"summary": "S", "recent": [m2], "triggered": True, "tokens": 20_000}

    monkeypatch.setattr("cognition.compressor.compress_context", _compress)
    upserts: list[tuple] = []
    monkeypatch.setattr("app.session.repository.get_epoch", lambda _sid: None)
    monkeypatch.setattr("app.session.repository.latest_seq", lambda _sid: 0)
    monkeypatch.setattr(
        "app.session.repository.upsert_epoch",
        lambda sid, baseline, seq, snapshot: upserts.append((sid, baseline, seq, snapshot)),
    )
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))
    sess = _sess()
    out = await _switch(_FakeCtx(sess, [m1, m2]), "new/model")
    assert out["summarized"] is True
    assert out["tail_start_id"] == "m2"
    assert calls and calls[0]["threshold"] == 6000
    assert upserts[0][3]["summary"] == "S"
    assert upserts[0][3]["tail_start_id"] == "m2"
    assert _audit_events(tmp_path)[-1]["summarized"] is True


async def test_switch_compression_failure_keeps_old_model(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 10000})
    monkeypatch.setattr("cognition.compressor.estimate_tokens", lambda _text: 10_000)
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))

    async def _compress(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr("cognition.compressor.compress_context", _compress)
    sess = _sess()
    ctx = _FakeCtx(sess, [HumanMessage(content="x", id="m1")])
    with pytest.raises(ModelSwitchError) as exc:
        await _switch(ctx, "new/model")
    assert exc.value.status_code == 500
    assert sess.model == "old/model"
    assert ctx.sessions.persisted == []
    events = _audit_events(tmp_path)
    assert events[-1]["status"] == "rolled_back"
    assert "boom" in events[-1]["error"]


async def test_switch_integrity_loss_rolls_back_and_audits(monkeypatch, tmp_path):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 128000})
    monkeypatch.setattr("cognition.compressor.estimate_tokens", lambda _text: 1)
    upserts: list[tuple] = []
    monkeypatch.setattr("app.session.repository.get_epoch", lambda _sid: None)
    monkeypatch.setattr("app.session.repository.latest_seq", lambda _sid: 0)
    monkeypatch.setattr(
        "app.session.repository.upsert_epoch",
        lambda sid, baseline, seq, snapshot: upserts.append((sid, baseline, seq, snapshot)),
    )
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))
    m1 = HumanMessage(content="keep", id="m1")
    m2 = HumanMessage(content="lost", id="m2")
    ctx = _FakeCtx(_sess(), [m1, m2])
    calls = {"n": 0}
    original = ctx._raw_messages

    async def _raw(_sid):
        calls["n"] += 1
        return await original(_sid) if calls["n"] == 1 else [m1]

    ctx._raw_messages = _raw
    with pytest.raises(ModelSwitchError) as exc:
        await _switch(ctx, "new/model")
    assert exc.value.status_code == 500
    assert "完整性" in str(exc.value)
    events = _audit_events(tmp_path)
    assert events[-1]["status"] == "rolled_back"
    assert "完整性" in events[-1]["error"]
    # 绑定写入后检测到丢失 → 回滚写入旧快照
    assert len(upserts) == 2


def test_initial_state_binds_stable_id_and_window(monkeypatch):
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 100000})
    state = initial_state(
        "q",
        "u1",
        model="m",
        message_id="um_abc",
        context_summary="summary",
        context_tail_start_id="m2",
    )
    assert state["messages"][0].id == "um_abc"
    assert state["context_summary"] == "summary"
    assert state["context_tail_start_id"] == "m2"
    assert state["context_window"] == 100000
    assert state["compress_threshold"] == 60000
    assert state["keep_recent_tokens"] == 20000


def test_initial_state_uses_bound_compression_config():
    state = initial_state(
        "q",
        model="m",
        context_window_override=10_000,
        compress_threshold_override=5_000,
        keep_recent_tokens_override=2_000,
    )
    assert state["context_window"] == 10_000
    assert state["compress_threshold"] == 5_000
    assert state["keep_recent_tokens"] == 2_000


def test_initial_state_does_not_override_existing_summary():
    state = initial_state("q", model=None)
    assert "context_summary" not in state


def test_initial_state_bound_can_clear_tail():
    state = initial_state(
        "q",
        model=None,
        context_summary="S",
        context_tail_start_id=None,
        context_bound=True,
    )
    assert state["context_summary"] == "S"
    assert state["context_tail_start_id"] is None


@pytest.mark.asyncio
async def test_resolve_binding_prefers_newer_graph_summary():
    ctx = _FakeCtx(_sess(), [])
    ctx.context_binding = lambda _sid: {
        "active": True,
        "summary": "old",
        "tail_start_id": "m1",
    }

    async def _state(_sid):
        return {"context_summary": "new", "context_tail_start_id": "m2"}

    ctx.state = _state
    out = await PlatformContext.resolve_context_binding(ctx, "ses_1")
    assert out == {
        "active": True,
        "summary": "new",
        "tail_start_id": "m2",
        "context_window": 0,
        "compress_threshold": 0,
        "keep_recent_tokens": 0,
    }


def test_stable_message_id_prefers_checkpoint_id():
    msg = HumanMessage(content="x", id="abc")
    assert PlatformContext.stable_message_id("ses", msg) == "abc"
    a = HumanMessage(content="same")
    b = HumanMessage(content="same")
    assert PlatformContext.stable_message_id("ses", a) == PlatformContext.stable_message_id("ses", b)
    assert PlatformContext.stable_message_id("ses", a).startswith("msg_")
