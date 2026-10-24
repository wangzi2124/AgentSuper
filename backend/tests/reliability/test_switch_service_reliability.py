"""REL: SwitchService fail-closed / rollback behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from harness.compression import CompressionError
from harness.switch import ModelSwitchError, SwitchDeps, SwitchService


class _Sess:
    def __init__(self, model="model/a", status="idle"):
        self.model = model
        self.status = status
        self.updated_at = 0.0


def _deps(tmp_path, *, sess=None, limits=None, messages=None):
    sess = sess or _Sess()
    messages = messages or [HumanMessage(content="small", id="m1")]
    calls = {"read": 0, "upserts": [], "persisted": 0}

    async def read_messages(_sid):
        calls["read"] += 1
        if calls["read"] == 1:
            return list(messages)
        return list(messages)

    def upsert_epoch(sid, baseline, baseline_seq, snapshot):
        calls["upserts"].append((sid, baseline, baseline_seq, dict(snapshot)))

    def persist(_sid):
        calls["persisted"] += 1

    deps = SwitchDeps(
        get_session=lambda _sid: sess,
        persist_session=persist,
        read_messages=read_messages,
        graph_state=lambda _sid: _empty_state(),
        context_binding=lambda _sid: {},
        read_limits=lambda _mid: limits if limits is not None else {"context_length": 1000},
        get_epoch=lambda _sid: None,
        upsert_epoch=upsert_epoch,
        stable_message_id=lambda _sid, m: getattr(m, "id", ""),
        backup_dir=str(tmp_path / "switches"),
    )
    return deps, sess, calls


async def _empty_state():
    return {}


@pytest.mark.asyncio
async def test_empty_target_is_rejected(tmp_path):
    deps, _, _ = _deps(tmp_path)
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "  ")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_session_and_same_model(tmp_path):
    deps, sess, _ = _deps(tmp_path)
    deps.get_session = lambda _sid: None
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "model/b")
    assert exc.value.status_code == 404

    deps, sess, _ = _deps(tmp_path)
    out = await SwitchService(deps).switch("s1", "model/a")
    assert out["switched"] is False


@pytest.mark.asyncio
async def test_unknown_window_and_running_gates(tmp_path):
    deps, _, _ = _deps(tmp_path, limits={})
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "model/b")
    assert exc.value.status_code == 400

    deps, _, _ = _deps(tmp_path, sess=_Sess(status="running"))
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "model/b")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_backup_failure_keeps_old_model(tmp_path, monkeypatch):
    deps, sess, calls = _deps(tmp_path)
    service = SwitchService(deps)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_backup", boom)
    with pytest.raises(ModelSwitchError) as exc:
        await service.switch("s1", "model/b")
    assert exc.value.status_code == 500
    assert sess.model == "model/a"
    assert calls["persisted"] == 0


@pytest.mark.asyncio
async def test_compression_failure_rolls_back(tmp_path, monkeypatch):
    long_messages = [HumanMessage(content="x" * 10000, id="m1")]
    deps, sess, _ = _deps(tmp_path, messages=long_messages)

    async def boom(*args, **kwargs):
        raise CompressionError("cannot preserve")

    monkeypatch.setattr("harness.switch.compress_messages", boom)
    with pytest.raises(ModelSwitchError):
        await SwitchService(deps).switch("s1", "model/b")
    assert sess.model == "model/a"


@pytest.mark.asyncio
async def test_upsert_failure_keeps_old_model(tmp_path):
    deps, sess, _ = _deps(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("epoch locked")

    deps.upsert_epoch = fail
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "model/b")
    assert exc.value.status_code == 500
    assert sess.model == "model/a"


@pytest.mark.asyncio
async def test_persist_failure_rolls_back_binding(tmp_path):
    deps, sess, calls = _deps(tmp_path)

    def fail(_sid):
        raise OSError("db locked")

    deps.persist_session = fail
    with pytest.raises(ModelSwitchError) as exc:
        await SwitchService(deps).switch("s1", "model/b")
    assert exc.value.status_code == 500
    assert sess.model == "model/a"
    assert calls["upserts"]


@pytest.mark.asyncio
async def test_integrity_loss_rolls_back(tmp_path):
    deps, sess, _ = _deps(tmp_path)
    first = True

    async def read_messages(_sid):
        nonlocal first
        if first:
            first = False
            return [HumanMessage(content="a", id="m1")]
        return [HumanMessage(content="b", id="m2")]

    deps.read_messages = read_messages
    with pytest.raises(ModelSwitchError):
        await SwitchService(deps).switch("s1", "model/b")
    assert sess.model == "model/a"
