# -*- coding: utf-8 -*-
"""API 层集成用例（覆盖率冲刺）：直接调用路由函数 + 假 app.state。

策略：不启动 FastAPI 应用（避免 lifespan 加载重模型/网络），而是把路由函数
当作普通 async 函数直接 invoke，传入携带假 `app.state` 的 Request 桩。
覆盖 app/api/* 六大 0% 模块 + session/router + services/kb_cleanup + task_manager。

运行：pytest tests/test_api_integration.py
"""
import asyncio
import io
import json
import os
import ssl
import sys
import types
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app import auth as auth_service
from app.api import auth as auth_api
from app.api import custom_tools as custom_tools_api
from app.api import documents as documents_api
from app.api import generated as generated_api
from app.api import permission as permission_api
from app.api import plugins as plugins_api
from app.api import responses as responses_api
from app.api import skills as skills_api
from app.api import vectors as vectors_api
from app.api import weather as weather_api
from app.api.config import SummarizationConfig, get_summarization_config, update_summarization_config
from app.config import settings
from app.permission import set_manager
from app.session import db as session_db
from app.session import repository as session_repo
from app.session import router as session_router
from app.session import service as session_service_mod
from app.session.deps import create_project_context, resolve_session_context
from app.session.models import RevertRequest, SessionCreate, SessionUpdate
from app.skills.custom_tools import CustomToolStore
from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader
from app.services.task_manager import TaskManager
from app.storage.file_store import FileStore

# ── 测试基桩 ─────────────────────────────────────────────────────────────


class FakeRequest:
    """路由函数所需的 Request 桩：routes 只访问 app.state / headers / client。"""

    def __init__(self, state, headers=None, host: str = "127.0.0.1"):
        self.app = types.SimpleNamespace(state=state)
        self.headers = headers or {}
        self.client = types.SimpleNamespace(host=host)


class FakeVectorStore:
    def __init__(self):
        self.chunks = []
        self.count = 0

    def get_chunks(self, offset=0, limit=50, document_id=None, query=None):
        rows = [c for c in self.chunks
                if (not document_id or c["metadata"].get("document_id") == document_id)]
        return rows, len(rows)

    def clear_all(self):
        n = self.count
        self.chunks = []
        self.count = 0
        return n

    def delete_by_metadata(self, key, value):
        before = len(self.chunks)
        self.chunks = [c for c in self.chunks
                       if c["metadata"].get(key) != value and c.get(key) != value]
        self.count = len(self.chunks)
        return before - len(self.chunks)

    def add(self, texts, metadatas, embeddings):
        for t, m in zip(texts, metadatas):
            self.chunks.append({"id": f"c{len(self.chunks)}", "text": t, "metadata": m})
        self.count = len(self.chunks)


class FakeChapterStore:
    def __init__(self):
        self.chapters = []

    def add_chapter(self, **kw):
        self.chapters.append(dict(kw))

    def clear(self):
        self.chapters = []

    def clear_all(self):
        return len(self.chapters)

    def delete_by_document(self, doc_id):
        self.chapters = [c for c in self.chapters if c.get("document_id") != doc_id]


class FakeBM25:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1

    def remove_by_metadata(self, key, value):
        return 0

    def add(self, texts, metadatas):
        return None


class FakeAgent:
    def __init__(self):
        self.tools = [types.SimpleNamespace(name="tool_hello", description="say hello")]
        self.refresh_calls = 0
        self.prompt_rebuilds = 0

    async def refresh_tools(self):
        self.refresh_calls += 1

    def rebuild_system_prompt(self):
        self.prompt_rebuilds += 1


class FakeDocProcessor:
    def process(self, file_path, doc_id, filename):
        return ([("测试文本", {"document_id": doc_id, "filename": filename})], [{
            "document_id": doc_id, "filename": filename,
            "chapter_number": 1, "chapter_title": "总章",
            "summary": "s", "parent_chunk_text": "测试文本",
        }])


class FakeEmbeddings:
    def embed_documents(self, texts, batch=32, on_progress=None):
        total = len(texts)
        for done in range(1, total + 1):
            if on_progress:
                on_progress(done, total)
        return [[0.1] * 8 for _ in texts]


def make_state(tmp_path, include_custom_tools=True):
    uploads = tmp_path / "uploads"
    plugins_dir = str(tmp_path / "plugins")
    skills_dir = str(tmp_path / "skills")
    pinned = str(tmp_path / "pinned_tools.json")
    tools = CustomToolStore(plugins_dir, pinned) if include_custom_tools else None
    return types.SimpleNamespace(
        vector_store=FakeVectorStore(),
        file_store=FileStore(str(uploads)),
        chapter_store=FakeChapterStore(),
        bm25_index=FakeBM25(),
        agent=FakeAgent(),
        skill_loader=SkillLoader(skills_dir),
        plugin_loader=PluginLoader(plugins_dir),
        custom_tools=tools,
        task_manager=TaskManager(),
        doc_processor=FakeDocProcessor(),
        embeddings=FakeEmbeddings(),
    )


def make_state_without_custom_tools(tmp_path):
    st = make_state(tmp_path, include_custom_tools=False)
    del st.custom_tools
    return st


def admin_req(state):
    return FakeRequest(state, headers={"Authorization": "Bearer tok-x"})


def req(state):
    return FakeRequest(state, headers={"X-User-Id": "u-test"})


@pytest.fixture
def priv(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok-x")


# ── responses.py 统一响应封装 ─────────────────────────────────────────────


def test_responses_helpers():
    assert responses_api.ok({"a": 1}) == {"code": 0, "message": "ok", "data": {"a": 1}}
    assert responses_api.fail(2, "boom") == {"code": 2, "message": "boom", "data": None}
    assert responses_api.api_result(1, "m", []) == {"code": 1, "message": "m", "data": []}


def test_api_error_payload():
    err = responses_api.ApiError(code=7, message="拒绝", status=403, data="x")
    assert err.code == 7 and err.message == "拒绝" and err.status == 403 and err.data == "x"


def test_error_response_json():
    resp = responses_api.error_response(3, "bad", 400, {"k": "v"})
    assert resp.status_code == 400
    body = json.loads(resp.body)
    assert body == {"code": 3, "message": "bad", "data": {"k": "v"}, "detail": "bad"}


# ── auth 路由 /app/api/auth.py ────────────────────────────────────────────


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_users_path", str(tmp_path / "auth_users.json"))
    monkeypatch.setattr(settings, "auth_token_ttl", 3600)
    auth_service._users_cache = None
    yield
    auth_service._users_cache = None


def test_auth_status_disabled(auth_env, monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", None)
    assert asyncio.run(auth_api.auth_status())["data"] == {"enabled": False}


def test_auth_status_enabled(auth_env):
    assert asyncio.run(auth_api.auth_status())["data"] == {"enabled": True}


def test_auth_register_device_and_token(auth_env):
    out = asyncio.run(auth_api.register(auth_api.RegisterRequest(user_id="u-dev", device_secret="secret1")))
    assert out["data"]["status"] == "registered"

    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.register(auth_api.RegisterRequest(user_id="u-dev", device_secret="other")))
    assert e.value.status_code == 409

    tok = asyncio.run(auth_api.issue_token(auth_api.TokenRequest(user_id="u-dev", device_secret="secret1")))
    assert tok["data"]["token"] and tok["data"]["expires_at"] > 0

    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.issue_token(auth_api.TokenRequest(user_id="u-other", device_secret="nope")))
    assert e.value.status_code == 401


def test_auth_account_register_login_me(auth_env):
    reg = asyncio.run(auth_api.account_register(
        auth_api.AccountRegisterRequest(username="alice_test", password="secret12")))
    assert reg["data"]["status"] == "registered" and reg["data"]["token"]
    uid = reg["data"]["user_id"]

    ok_req = FakeRequest(None, headers={"X-User-Id": uid, "X-Auth-Token": reg["data"]["token"]})
    me = asyncio.run(auth_api.account_me(ok_req))
    assert me["data"]["username"] == "alice_test" and me["data"]["account_type"] == "account"

    bad = FakeRequest(None, headers={"X-User-Id": uid, "X-Auth-Token": "bad"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.account_me(bad))
    assert e.value.status_code == 401

    missing = FakeRequest(None, headers={"X-User-Id": "u-ghost", "X-Auth-Token": "ghost-token"})
    ghost_tok, _ = auth_service.issue_token("u-ghost")
    missing = FakeRequest(None, headers={"X-User-Id": "u-ghost", "X-Auth-Token": ghost_tok})
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.account_me(missing))
    assert e.value.status_code == 404

    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.account_register(
            auth_api.AccountRegisterRequest(username="alice_test", password="secret12")))
    assert e.value.status_code == 409

    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.account_login(
            auth_api.AccountLoginRequest(username="alice_test", password="wrong")))
    assert e.value.status_code == 401

    login = asyncio.run(auth_api.account_login(
        auth_api.AccountLoginRequest(username="alice_test", password="secret12")))
    assert login["data"]["user_id"] == uid


def test_auth_register_empty_disabled(auth_env, monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.register(auth_api.RegisterRequest(user_id="u1", device_secret="s")))
    assert e.value.status_code == 400  # 未启用

    with pytest.raises(HTTPException) as e:
        asyncio.run(auth_api.register(auth_api.RegisterRequest(user_id="", device_secret="")))
    assert e.value.status_code == 400


# ── config 路由 /app/api/config.py ────────────────────────────────────────


def test_summarization_get(monkeypatch):
    monkeypatch.setattr(settings, "summarization_model", "ollama/qwen")
    monkeypatch.setattr(settings, "summarization_keep_messages", 5)
    st = asyncio.run(get_summarization_config())
    assert st.model == "ollama/qwen" and st.enabled


def test_summarization_update(priv):
    body = SummarizationConfig(model="deepseek/deepseek-chat", keep_messages=0)
    st = asyncio.run(update_summarization_config(body, admin_req(types.SimpleNamespace())))
    assert st.model == "deepseek/deepseek-chat" and st.keep_messages == 1

    body2 = SummarizationConfig(model="", keep_messages=3)
    st2 = asyncio.run(update_summarization_config(body2, admin_req(types.SimpleNamespace())))
    assert st2.model is None and st2.keep_messages == 3 and not st2.enabled


# ── vectors 路由 /app/api/vectors.py ──────────────────────────────────────


def test_list_chunks(tmp_path):
    st = make_state(tmp_path)
    st.vector_store.add(["a"], [{"document_id": "d1"}], [[0.1] * 8])
    resp = asyncio.run(vectors_api.list_chunks(req(st), offset=0, limit=50,
                                                document_id="d1", query=None))
    assert resp.total == 1 and resp.chunks[0].metadata["document_id"] == "d1"


def test_vector_store_config_counts(tmp_path):
    st = make_state(tmp_path)
    doc_id, _ = st.file_store.save("a.txt", b"x")
    st.file_store.mark_index_state(doc_id, "ready", chunk_count=2)
    st.vector_store.count = 7
    cfg = asyncio.run(vectors_api.vector_store_config(req(st)))
    assert cfg["count"] == 7 and cfg["index_states"] == {"ready": 1} and cfg["pending_repair"] == 0


def test_clear_vector_store(tmp_path, priv):
    st = make_state(tmp_path)
    st.vector_store.add(["a"], [{"document_id": "d1"}], [[0.1] * 8])
    doc_id, _ = st.file_store.save("a.txt", b"x")
    st.chapter_store.add_chapter(document_id=doc_id)
    res = asyncio.run(vectors_api.clear_vector_store(admin_req(st)))
    assert res["removed_vectors"] == 1
    assert st.vector_store.count == 0 and len(st.file_store.list_all()) == 0


def test_clear_expired_vectors(tmp_path, priv, monkeypatch):
    st = make_state(tmp_path)
    doc_id, _ = st.file_store.save("old.txt", b"x")
    st.file_store.update_meta(doc_id, {"created_at": "2020-01-01T00:00:00"})
    monkeypatch.setattr(settings, "vector_store_ttl_days", 1)
    res = asyncio.run(vectors_api.clear_expired_vectors(admin_req(st)))
    assert res["removed"] == 1 and res["ttl_days"] == 1

    monkeypatch.setattr(settings, "vector_store_ttl_days", 0)
    res0 = asyncio.run(vectors_api.clear_expired_vectors(admin_req(st)))
    assert res0["removed"] == 0


def test_repair_vectors(tmp_path, priv, monkeypatch):
    st = make_state(tmp_path)

    async def fake_repair(app_state):
        return {"repaired": ["d1"], "failed": []}

    monkeypatch.setattr("app.services.kb_repair.repair_incomplete_documents", fake_repair)
    res = asyncio.run(vectors_api.repair_vectors(admin_req(st)))
    assert "已重建 1 个文档" in res["message"] and res["repaired"] == ["d1"]


# ── skills 路由 /app/api/skills.py ────────────────────────────────────────


def _write_skill(loader, name="test-skill"):
    path = loader.skills_dir / "test-skill.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: test-skill\ndescription: demo\nenabled: true\n---\n# body\n",
        encoding="utf-8",
    )


def test_skills_list_and_toggle(tmp_path, priv):
    st = make_state(tmp_path)
    _write_skill(st.skill_loader)
    st.skill_loader.load_all()
    items = asyncio.run(skills_api.list_skills(req(st)))
    assert any(s["name"] == "test-skill" for s in items)

    res = asyncio.run(skills_api.toggle_skill("test-skill", skills_api.ToggleSkillRequest(enabled=False), admin_req(st)))
    assert "disabled" in res["message"] and st.agent.refresh_calls == 1

    with pytest.raises(HTTPException) as e:
        asyncio.run(skills_api.toggle_skill("ghost", skills_api.ToggleSkillRequest(enabled=True), admin_req(st)))
    assert e.value.status_code == 404


# ── plugins 路由 /app/api/plugins.py ──────────────────────────────────────


def _write_plugin(loader, name="example-plugin"):
    path = loader.plugins_dir / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'PLUGIN_NAME = "example-plugin"\n'
        'PLUGIN_VERSION = "1.0.0"\n'
        'PLUGIN_DESCRIPTION = "demo"\n'
        'def tool_hello(name="world"):\n'
        '    return {"greeting": "hi " + name}\n',
        encoding="utf-8",
    )
    (loader.plugins_dir / f"{name}.enabled").touch()


def test_plugins_list_status_call(tmp_path, priv):
    st = make_state(tmp_path)
    _write_plugin(st.plugin_loader)
    st.plugin_loader.load_all()

    items = asyncio.run(plugins_api.list_plugins(req(st)))
    assert items[0]["name"] == "example-plugin"

    s = asyncio.run(plugins_api.get_plugin_status("example-plugin", req(st)))
    assert s["enabled"] is True
    with pytest.raises(HTTPException) as e:
        asyncio.run(plugins_api.get_plugin_status("nope", req(st)))
    assert e.value.status_code == 404

    called = asyncio.run(plugins_api.call_plugin_function(
        "example-plugin", "tool_hello",
        plugins_api.CallPluginRequest(args={"name": "张三"}), admin_req(st)))
    assert called["result"] == {"greeting": "hi 张三"}

    with pytest.raises(HTTPException) as e:
        asyncio.run(plugins_api.call_plugin_function(
            "file_reader", "tool_read_file",
            plugins_api.CallPluginRequest(args={}), admin_req(st)))
    assert e.value.status_code == 403


def test_plugins_toggle(tmp_path, priv):
    st = make_state(tmp_path)
    _write_plugin(st.plugin_loader)
    st.plugin_loader.load_all()
    res = asyncio.run(plugins_api.toggle_plugin("example-plugin", plugins_api.TogglePluginRequest(enabled=False), admin_req(st)))
    assert "disabled" in res["message"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(plugins_api.toggle_plugin("ghost", plugins_api.TogglePluginRequest(enabled=True), admin_req(st)))
    assert e.value.status_code == 404


# ── custom_tools 路由 /app/api/custom_tools.py ────────────────────────────


def test_custom_tools_crud(tmp_path, priv):
    st = make_state(tmp_path)
    assert asyncio.run(custom_tools_api.list_custom_tools(req(st))) == []

    cat = asyncio.run(custom_tools_api.tool_catalog(req(st)))
    assert any(t["name"] == "tool_hello" for t in cat)

    script = "def tool_echo(text: str = ''):\n    return {'echo': text}"
    created = asyncio.run(custom_tools_api.create_script(
        custom_tools_api.ScriptRequest(name="echo-tool", description="echo", script=script), admin_req(st)))
    assert created["name"] == "echo-tool"

    with pytest.raises(HTTPException) as e:
        asyncio.run(custom_tools_api.create_script(
            custom_tools_api.ScriptRequest(name="echo-tool", description="echo", script=script), admin_req(st)))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        asyncio.run(custom_tools_api.create_script(
            custom_tools_api.ScriptRequest(name="bad", description="x", script="def f(): return 1"), admin_req(st)))
    assert e.value.status_code == 400

    pinned = asyncio.run(custom_tools_api.create_pin(
        custom_tools_api.PinRequest(tool_name="tool_hello"), admin_req(st)))
    assert pinned["tool_name"] == "tool_hello"
    with pytest.raises(HTTPException) as e:
        asyncio.run(custom_tools_api.create_pin(
            custom_tools_api.PinRequest(tool_name="tool_hello"), admin_req(st)))
    assert e.value.status_code == 400

    toggled = asyncio.run(custom_tools_api.toggle_custom("tool_hello", custom_tools_api.ToggleRequest(enabled=False), admin_req(st)))
    assert "disabled" in toggled["message"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(custom_tools_api.toggle_custom("ghost", custom_tools_api.ToggleRequest(enabled=True), admin_req(st)))
    assert e.value.status_code == 404

    deleted = asyncio.run(custom_tools_api.delete_custom("echo-tool", admin_req(st)))
    assert "deleted" in deleted["message"]


def test_custom_tools_missing_store(tmp_path):
    st = make_state_without_custom_tools(tmp_path)
    with pytest.raises(HTTPException) as e:
        asyncio.run(custom_tools_api.list_custom_tools(req(st)))
    assert e.value.status_code == 500


# ── generated 路由 /app/api/generated.py ──────────────────────────────────


def test_generated_list_download_delete(tmp_path, monkeypatch):
    gen_dir = tmp_path / "gen"
    gen_dir.mkdir()
    (gen_dir / "report.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(generated_api, "GENERATED_DIR", gen_dir)

    listing = asyncio.run(generated_api.list_generated())
    assert listing.total == 1 and listing.files[0].filename == "report.txt"
    assert asyncio.run(generated_api.list_generated(q="rep")).total == 1
    assert asyncio.run(generated_api.list_generated(q="zzz")).total == 0

    dl = asyncio.run(generated_api.download_generated("report.txt"))
    assert dl.filename == "report.txt"
    for bad in ("missing.txt", "../secret.txt", ""):
        with pytest.raises(HTTPException):
            asyncio.run(generated_api.download_generated(bad))

    res = asyncio.run(generated_api.delete_generated("report.txt"))
    assert "Deleted" in res["message"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(generated_api.delete_generated("report.txt"))
    assert e.value.status_code == 404


# ── documents 路由 /app/api/documents.py ──────────────────────────────────


def _upload(name, content: bytes):
    return UploadFile(filename=name, file=io.BytesIO(content))


def test_documents_upload_flow(tmp_path):
    st = make_state(tmp_path)

    async def upload_and_wait():
        resp = await documents_api.upload_document(req(st), _upload("sample.txt", b"hello world"))
        pending = getattr(st, "_doc_processing_tasks", set())
        if pending:
            await asyncio.gather(*list(pending))
        return resp

    resp = asyncio.run(upload_and_wait())
    progress = asyncio.run(documents_api.get_task_progress(req(st), resp.task_id))
    assert progress.status == "completed"
    assert progress.result.chunk_count == 1

    listing = asyncio.run(documents_api.list_documents(req(st)))
    assert listing.total == 1

    with pytest.raises(HTTPException) as e:
        asyncio.run(documents_api.get_task_progress(req(st), "no-such-id"))
    assert e.value.status_code == 404


def test_documents_empty_and_oversize(tmp_path, monkeypatch):
    st = make_state(tmp_path)
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents_api.upload_document(req(st), _upload("empty.txt", b"")))
    assert e.value.status_code == 400

    monkeypatch.setattr(documents_api, "MAX_UPLOAD_SIZE", 10)
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents_api.upload_document(req(st), _upload("big.txt", b"x" * 20)))
    assert e.value.status_code == 413


def test_documents_delete(tmp_path):
    st = make_state(tmp_path)
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents_api.delete_document(req(st), "nope"))
    assert e.value.status_code == 404

    doc_id, _ = st.file_store.save("del.txt", b"x")
    st.vector_store.add(["t"], [{"document_id": doc_id}], [[0.1] * 8])
    res = asyncio.run(documents_api.delete_document(req(st), doc_id))
    assert "deleted" in res.message.lower()
    assert st.file_store.get(doc_id) is None and st.vector_store.count == 0

    with pytest.raises(HTTPException) as e:
        asyncio.run(documents_api.delete_document(req(st), doc_id))
    assert e.value.status_code == 404


# ── permission 路由 /app/api/permission.py ────────────────────────────────


class FakePermissionManager:
    def __init__(self):
        self.pending = []
        self.workspaces = []

    def get_pending_requests(self):
        return [
            types.SimpleNamespace(id=r["id"], path=r["path"], operation=r["operation"],
                                  tool_name=r["tool_name"], tool_args=r["tool_args"],
                                  created_at=r["created_at"])
            for r in self.pending
        ]

    def respond(self, request_id, decision, remember=False):
        for i, r in enumerate(self.pending):
            if r["id"] == request_id:
                self.pending.pop(i)
                return True
        return False

    def add_workspace(self, path):
        self.workspaces.append(path)
        return path

    def remove_workspace(self, path):
        if path in self.workspaces:
            self.workspaces.remove(path)
            return True
        return False

    def list_workspaces(self):
        return [{"path": p, "type": "extra"} for p in self.workspaces]


@pytest.fixture
def perm_fx(tmp_path, monkeypatch):
    fake = FakePermissionManager()
    monkeypatch.setattr(settings, "admin_token", "tok-x")
    monkeypatch.setattr("app.permission.manager.get_manager", lambda: fake)
    monkeypatch.setattr("app.api.permission.get_manager", lambda: fake)
    yield fake
    set_manager(None)


def test_permission_pending_and_respond(tmp_path, perm_fx):
    st = make_state(tmp_path)
    perm_fx.pending.append({
        "id": "req1", "path": "C:/x", "operation": "write",
        "tool_name": "tool_write_file", "tool_args": {}, "created_at": datetime.now(),
    })
    pending = asyncio.run(permission_api.list_pending(admin_req(st)))
    assert len(pending["pending"]) == 1 and pending["pending"][0]["created_at"]

    ok = asyncio.run(permission_api.respond(
        "req1", permission_api.RespondRequest(decision="allowed", remember=True), admin_req(st)))
    assert ok == {"status": "ok"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(permission_api.respond(
            "req1", permission_api.RespondRequest(decision="denied"), admin_req(st)))
    assert e.value.status_code == 404


def test_permission_browse_dirs(tmp_path):
    st = make_state(tmp_path)
    (tmp_path / "sub").mkdir()
    result = asyncio.run(permission_api.browse_dirs(admin_req(st), path=str(tmp_path)))
    assert any(d["name"] == "sub" for d in result["dirs"])
    root = asyncio.run(permission_api.browse_dirs(admin_req(st), path=""))
    assert "dirs" in root
    with pytest.raises(HTTPException) as e:
        asyncio.run(permission_api.browse_dirs(admin_req(st), path=str(tmp_path / "nothing")))
    assert e.value.status_code == 400


def test_permission_workspaces(tmp_path, perm_fx):
    st = make_state(tmp_path)
    assert asyncio.run(permission_api.list_workspaces())["workspaces"] == []

    with pytest.raises(HTTPException) as e:
        asyncio.run(permission_api.add_workspace(permission_api.WorkspaceRequest(path=""), admin_req(st)))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        asyncio.run(permission_api.add_workspace(permission_api.WorkspaceRequest(path="relative"), admin_req(st)))
    assert e.value.status_code == 400

    abs_path = str(tmp_path / "ws")
    res = asyncio.run(permission_api.add_workspace(permission_api.WorkspaceRequest(path=abs_path), admin_req(st)))
    assert res["status"] == "ok" and st.agent.prompt_rebuilds == 1

    removed = asyncio.run(permission_api.remove_workspace(admin_req(st), path=abs_path))
    assert removed["status"] == "ok"
    with pytest.raises(HTTPException) as e:
        asyncio.run(permission_api.remove_workspace(admin_req(st), path=abs_path))
    assert e.value.status_code == 404


def test_permission_rebuild_prompt_silent_on_no_method(tmp_path, perm_fx):
    st = make_state(tmp_path)
    st.agent = types.SimpleNamespace()  # 无 rebuild_system_prompt 方法
    res = asyncio.run(permission_api.add_workspace(
        permission_api.WorkspaceRequest(path=str(tmp_path / "ws2")), admin_req(st)))
    assert res["status"] == "ok"


# ── weather 路由 /app/api/weather.py ──────────────────────────────────────


def _fake_fetch_json():
    def _f(url, timeout=10):
        if "geocoding-api" in url:
            return {"results": [{"name": "北京", "country": "中国", "admin1": "北京",
                                 "latitude": 39.9, "longitude": 116.4}]}
        if "forecast" in url:
            return {
                "current": {"weather_code": 0, "temperature_2m": 21.5,
                            "apparent_temperature": 20.0, "relative_humidity_2m": 40,
                            "wind_speed_10m": 3.0, "wind_direction_10m": 90,
                            "pressure_msl": 1013, "precipitation": 0.0},
                "daily": {"time": ["2026-08-29", "2026-08-30", "2026-08-31"],
                          "weather_code": [0, 3, 61],
                          "temperature_2m_max": [30, 29, 27],
                          "temperature_2m_min": [20, 19, 18],
                          "precipitation_sum": [0, 1, 8],
                          "wind_speed_10m_max": [5, 6, 7],
                          "sunrise": ["05:00"] * 3, "sunset": ["19:00"] * 3},
            }
        return {"city": "北京", "regionName": "北京"}
    return _f


def test_weather_code_helpers():
    assert weather_api._weather_code_desc(0) == "晴"
    assert weather_api._weather_code_desc(999) == "未知 (999)"
    assert weather_api._weather_code_icon(61) == "🌧️"
    assert weather_api._weather_code_icon(999) == "🌡️"


def test_weather_ssl_context():
    assert isinstance(weather_api._create_ssl_context(), ssl.SSLContext)


def test_weather_fetch_weather(monkeypatch):
    monkeypatch.setattr(weather_api, "_fetch_json", _fake_fetch_json())
    data = weather_api._fetch_weather("北京")
    assert data["location"]["name"] == "北京"
    assert data["current"]["temperature"] == 21.5
    assert data["current"]["condition"] == "晴"
    assert len(data["forecast"]) == 3


def test_weather_geocode_no_results(monkeypatch):
    monkeypatch.setattr(weather_api, "_fetch_json", lambda url, timeout=10: {"results": []})
    with pytest.raises(ValueError):
        weather_api._geocode_cn("不存在城")


def test_weather_get_and_refresh(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok-x")
    cache_snapshot = dict(weather_api._cache)
    monkeypatch.setattr(weather_api, "_cache", dict(cache_snapshot))
    weather_api._cache["error"] = "boom"
    with pytest.raises(HTTPException) as e:
        asyncio.run(weather_api.get_weather())
    assert e.value.status_code == 503

    weather_api._cache["error"] = None
    weather_api._cache["weather"] = {"temperature": 1}
    weather_api._cache["city"] = "上海"
    weather_api._cache["updated_at"] = 1700000000
    got = asyncio.run(weather_api.get_weather())
    assert got["city"] == "上海" and got["weather"] == {"temperature": 1} and got["updated_at_fmt"]

    monkeypatch.setattr(weather_api, "_fetch_json", _fake_fetch_json())
    refreshed = asyncio.run(weather_api.refresh_weather_endpoint(weather_api.RefreshRequest(city="北京")))
    assert refreshed["city"] == "北京" and refreshed["weather"]["location"]["name"] == "北京"

    monkeypatch.setattr(weather_api, "_fetch_json", lambda url, timeout=10: (_ for _ in ()).throw(OSError("down")))
    failed = asyncio.run(weather_api.refresh_weather_endpoint(weather_api.RefreshRequest()))
    assert failed["error"]


def test_weather_load_on_startup(monkeypatch):
    captured = {}

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, **kw):
            captured["target"] = target
            captured["args"] = args
            captured["kwargs"] = kwargs or {}

        def start(self):
            return self

    monkeypatch.setattr(weather_api.threading, "Thread", FakeThread)
    monkeypatch.setattr(weather_api, "_fetch_json", _fake_fetch_json())
    weather_api.load_weather_on_startup()
    assert captured["target"] is not None
    captured["target"](*captured["args"], **captured["kwargs"])
    assert weather_api._cache["city"] and weather_api._cache["weather"]["current"]["condition"]

    monkeypatch.setattr(weather_api, "_fetch_json", lambda url, timeout=10: (_ for _ in ()).throw(OSError("fail")))
    captured.clear()
    weather_api.load_weather_on_startup()
    captured["target"](*captured["args"], **captured["kwargs"])
    assert weather_api._cache["error"]


# ── session/router.py（临时 DB 隔离）──────────────────────────────────────


@pytest.fixture
def session_fx(tmp_path, monkeypatch):
    monkeypatch.setattr(session_db, "DB_PATH", tmp_path / "session.db")
    session_db._pool._idle.clear()
    session_db._pool._created = 0
    session_db.init_db()
    svc = session_service_mod.SessionService()
    st = types.SimpleNamespace(session_service=svc, agent=FakeAgent())
    yield svc, st, req(st)
    session_db._pool._idle.clear()
    session_db._pool._created = 0


def _create(svc, uid="u-test"):
    return svc.create(uid, project_id=None, directory="C:\\workx", kind="multi-agent", title="t")


def test_session_crud_and_messages(session_fx):
    svc, st, rq = session_fx
    sess_id = _create(svc).id

    listed = asyncio.run(session_router.list_sessions(rq))
    assert any(s.id == sess_id for s in listed)

    ctx = asyncio.run(resolve_session_context(rq, sess_id))
    got = asyncio.run(session_router.get_session(rq, ctx))
    assert got.id == sess_id

    updated = asyncio.run(session_router.update_session(
        SessionUpdate(title="renamed", agent="rag", model=None, archived=None), ctx))
    assert updated.title == "renamed"

    m1 = svc.append_message("u-test", sess_id, "user", {"content": "hi"})
    m2 = svc.append_message("u-test", sess_id, "assistant", {"content": "hello"})
    session_repo.append_part(sess_id, m1.id, "text", {"text": "hi part"})

    msgs = asyncio.run(session_router.list_messages(rq, after_seq=0, limit=None, ctx=ctx))
    assert len(msgs) == 2 and msgs[0]["parts"][0]["type"] == "text"

    ctxt = asyncio.run(session_router.session_context(ctx))
    assert ctxt["session_id"] == sess_id and ctxt["epoch"] is None

    status = asyncio.run(session_router.status(ctx))
    assert status.session_id == sess_id
    assert asyncio.run(session_router.children(ctx)) == []

    with pytest.raises(HTTPException) as e:
        asyncio.run(session_router.delete_message("ghost-msg", ctx))
    assert e.value.status_code == 404

    asyncio.run(session_router.delete_message(m2.id, ctx))
    assert len(svc.messages("u-test", sess_id)) == 1


def test_session_fork_compact_revert_intr(session_fx):
    svc, st, rq = session_fx
    sess_id = _create(svc).id
    m1 = svc.append_message("u-test", sess_id, "user", {"content": "q1"})
    m2 = svc.append_message("u-test", sess_id, "assistant", {"content": "a1"})
    session_repo.append_part(sess_id, m1.id, "text", {"text": "q1 part"})
    ctx = asyncio.run(resolve_session_context(rq, sess_id))

    child = asyncio.run(session_router.fork_session(rq, message_id=m2.id, ctx=ctx))
    assert child.parent_id == sess_id
    child_msgs = svc.messages("u-test", child.id)
    assert len(child_msgs) == 2 and session_repo.list_parts(child_msgs[0].id)

    comp = asyncio.run(session_router.compact_session(rq, checkpoint="sum", ctx=ctx))
    assert comp is None
    assert svc.messages("u-test", sess_id)[-1].type == "compaction"

    rev = asyncio.run(session_router.revert_session(RevertRequest(message_id=m2.id), ctx))
    assert rev["deleted"] >= 1 and m1.id in [m["id"] for m in rev["messages"]]

    asyncio.run(session_router.interrupt_session(ctx))
    assert svc.get("u-test", sess_id).status == "interrupted"

    asyncio.run(session_router.delete_session(ctx))
    with pytest.raises(HTTPException) as e:
        asyncio.run(resolve_session_context(rq, sess_id))
    assert e.value.status_code == 404


def test_session_parent_creation(session_fx):
    svc, st, rq = session_fx
    parent = _create(svc)
    body = SessionCreate(
        project_id=None, directory="", parent_id=parent.id,
        agent="web_search", model=None, kind="multi-agent", title="child",
    )
    child = asyncio.run(session_router.create_session(body, rq))
    assert child.parent_id == parent.id

    uid, pid = create_project_context(rq, None)
    assert uid == "u-test" and pid


# ── services/task_manager.py（成功 + 失败路径）─────────────────────────────


def test_task_manager_success(tmp_path):
    tm = TaskManager()
    fs = FileStore(str(tmp_path / "uploads"))
    tid = tm.create("a.txt")
    vs = FakeVectorStore()
    cs = FakeChapterStore()

    async def run():
        await tm.process_document(tid, b"data", "a.txt", fs, FakeDocProcessor(),
                                  FakeEmbeddings(), vs, FakeBM25(), cs)

    asyncio.run(run())
    task = tm.get(tid)
    assert task.status == "completed" and task.progress == 100
    assert task.result["chunk_count"] == 1
    assert len(cs.chapters) == 1 and vs.count == 1


def test_task_manager_failure_cleanup(tmp_path):
    tm = TaskManager()
    fs = FileStore(str(tmp_path / "uploads"))
    tid = tm.create("bad.bin")

    class FailingProc:
        def process(self, file_path, doc_id, filename):
            raise ValueError("corrupt file")

    async def run():
        vs = FakeVectorStore()
        cs = FakeChapterStore()
        await tm.process_document(tid, b"\x00", "bad.bin", fs, FailingProc(),
                                  FakeEmbeddings(), vs, None, cs)

    asyncio.run(run())
    task = tm.get(tid)
    assert task.status == "failed" and "corrupt" in task.error
    doc_id = fs.list_all()[0]["id"]
    assert fs.get(doc_id)["index_state"] in ("failed", "pending")

    asyncio.run(tm.process_document("ghost", b"", "x", fs, FakeDocProcessor(),
                                    FakeEmbeddings(), FakeVectorStore()))


def test_task_manager_prune(tmp_path):
    tm = TaskManager()
    tid = tm.create("x.txt")
    task = tm.get(tid)
    task.status = "completed"
    task.finished_at = datetime.fromtimestamp(0)  # 远超 TTL
    tm._prune_old_tasks()
    assert tm.get(tid) is None  # 过期任务被清理


# ── services/kb_cleanup.py + storage/paths.py ─────────────────────────────


def test_kb_cleanup_delete_and_lock(tmp_path):
    from app.services import kb_cleanup
    st = types.SimpleNamespace()
    st.vector_store = FakeVectorStore()
    st.chapter_store = FakeChapterStore()
    st.bm25_index = FakeBM25()
    st.file_store = FileStore(str(tmp_path / "uploads"))

    lock1 = kb_cleanup.get_processing_lock(st)
    lock2 = kb_cleanup.get_processing_lock(st)
    assert lock1 is lock2

    doc_id, _ = st.file_store.save("a.txt", b"x")
    st.vector_store.add(["t"], [{"document_id": doc_id}], [[0.1] * 8])
    kb_cleanup.delete_document_data(st, doc_id)
    assert st.file_store.get(doc_id) is None and st.vector_store.count == 0

    kb_cleanup.delete_document_data(st, "nope")  # 静默


def test_kb_cleanup_clear_all(tmp_path):
    from app.services import kb_cleanup
    st = types.SimpleNamespace()
    st.vector_store = FakeVectorStore()
    st.chapter_store = FakeChapterStore()
    st.bm25_index = FakeBM25()
    st.file_store = FileStore(str(tmp_path / "uploads"))
    st.vector_store.add(["t"], [{"document_id": "d1"}], [[0.1] * 8])
    st.file_store.save("b.txt", b"y")

    from app.storage import paths as storage_paths  # noqa: F401
    res = asyncio.run(kb_cleanup.clear_all_kb(st))
    assert res["removed_vectors"] == 1 and len(st.file_store.list_all()) == 0


def test_storage_paths_env_override(tmp_path, monkeypatch):
    from app.storage import paths as storage_paths
    monkeypatch.setenv("AGENTSUPER_DATA", str(tmp_path / "data_x"))
    g = storage_paths.global_paths()
    assert g["data"] == tmp_path / "data_x"

    scoped = storage_paths.project_scoped("../evil/../name")
    assert ".." not in Path(scoped["session"]).parts


# ── storage/file_store.py 关键分支 ────────────────────────────────────────


def test_file_store_corrupt_meta(tmp_path):
    meta = tmp_path / "uploads" / "metadata.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text("{not json", encoding="utf-8")
    fs = FileStore(str(tmp_path / "uploads"))
    assert fs.metadata == {}


def test_file_store_ready_state_and_clear(tmp_path):
    fs = FileStore(str(tmp_path / "uploads"))
    doc_id, _ = fs.save("a.txt", b"x")
    fs.mark_index_state(doc_id, "ready", chunk_count=3)
    assert fs.get(doc_id)["index_state"] == "ready" and fs.get(doc_id)["chunk_count"] == 3
    fs.mark_index_state("ghost", "ready")  # 不存在的文档：静默
    assert fs.clear_all() == 1
    assert fs.list_all() == []


# ── custom_tools store 纯逻辑 ─────────────────────────────────────────────


def test_custom_tool_store_slug_and_pins(tmp_path):
    store = CustomToolStore(str(tmp_path / "plugins"), str(tmp_path / "pinned.json"))
    assert store._slug("my tool") == "my_tool"
    with pytest.raises(ValueError):
        store._slug("..")
    assert store._slug("_hidden") == "hidden"  # 前导下划线被 strip 掉

    store.create_pin("tool_ls", "list")
    assert store.pinned_tools() == ["tool_ls"]
    assert store.toggle("tool_ls", False)
    assert store.pinned_tools() == []
    assert store.toggle("nope", True) is False
    assert store.remove("tool_ls")
    assert store.remove("nope") is False
    assert store.list() == []