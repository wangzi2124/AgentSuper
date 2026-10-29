"""关键路由挂载回归：/api/plugins 必须存在（前端插件页依赖）。"""

from __future__ import annotations

from app.main import _include_app_routers


class _FakeApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def include_router(self, router, *, prefix: str = "", tags=None) -> None:  # noqa: ANN001
        self.calls.append((prefix, tuple(tags or ())))


def test_plugins_router_is_mounted():
    app = _FakeApp()
    _include_app_routers(app)
    prefixes = [p for p, _ in app.calls]
    assert "/api/plugins" in prefixes
