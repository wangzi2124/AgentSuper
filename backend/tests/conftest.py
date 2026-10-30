"""共享测试夹具（agent.md §9 tests/，§10 测试方案）。

提供 `FakeModel`（TierModel 契约的确定性假模型）与 `client`（轻量 TestClient），
供 e2e / security / perf 等分层测试复用；`create_app(include_app=False)` 只装配
平台路由 + 注入假模型，避免拉起 ChromaDB / 嵌入模型等重运行时。
"""

from __future__ import annotations

import os
import tempfile

# 数据目录与真实用户目录隔离 —— 必须在导入 app.* 之前设置：
# - AGENTSUPER_DATA：统一数据根（session/checkpoint/vector/日志等全部落测试目录）
# - AGENTSUPER_MIGRATE_LEGACY=0：禁止把 backend/data 自动拷贝进临时目录
# - SESSION_DB_PATH / CHECKPOINT_DB_PATH / MEMORY_PERSIST_PATH：显式覆盖（沿用旧语义）
_TEST_DB_DIR = tempfile.mkdtemp(prefix="agentsuper-test-")
os.environ["AGENTSUPER_DATA"] = _TEST_DB_DIR
os.environ["AGENTSUPER_MIGRATE_LEGACY"] = "0"
os.environ["SESSION_DB_PATH"] = os.path.join(_TEST_DB_DIR, "session.db")
os.environ["CHECKPOINT_DB_PATH"] = os.path.join(_TEST_DB_DIR, "checkpoints.db")
os.environ["MEMORY_PERSIST_PATH"] = os.path.join(_TEST_DB_DIR, "agent_memory.db")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from execution.filesystem import VirtualFileSystem
from models.router import ChatResult

# e2e/perf 拒绝真实网络：假模型恒返回固定答案。
FAKE_ANSWER = "最终答案"


class FakeModel:
    """始终返回固定答案的假模型（TierModel 契约）。"""

    def __init__(self, content: str = FAKE_ANSWER) -> None:
        self.tier = "fake"
        self._content = content

    async def ainvoke(self, messages, *, tools=None, **kwargs):  # noqa: ANN001
        return ChatResult(content=self._content, finish_reason="stop")


@pytest.fixture(autouse=True)
def _isolate_monitor_metrics(tmp_path, monkeypatch):
    """把监控落盘重定向到 tmp：测试的 model/request 记录不再污染 data/ 生产指标。"""
    import observability.monitor as monitor

    monkeypatch.setattr(monitor, "_METRICS_FILE", tmp_path / "metrics.jsonl")
    monkeypatch.setattr(monitor, "_STATS_FILE", tmp_path / "monitor_stats.json")
    monkeypatch.setattr(monitor, "_metrics_buffer", [])
    monkeypatch.setattr(monitor, "_last_metrics_flush", 0.0)
    monkeypatch.setattr(monitor, "_last_metrics_prune", 0.0)


@pytest.fixture
def fake_model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def client(tmp_path):
    app = create_app(model=FakeModel(), vfs=VirtualFileSystem(root=str(tmp_path)))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_factory():
    """按需构造多个隔离的 TestClient（各自独立 workspace）。"""

    created: list[TestClient] = []

    def _make(content: str = FAKE_ANSWER) -> TestClient:
        app = create_app(
            model=FakeModel(content),
            vfs=VirtualFileSystem(root=tempfile.mkdtemp()),
        )
        c = TestClient(app)
        c.__enter__()
        created.append(c)
        return c

    yield _make

    for c in created:
        c.__exit__(None, None, None)
