"""G-09: plugin hooks, manifests and unload."""

from __future__ import annotations

import asyncio

from extensions.hooks import HookBus
from extensions.plugins import PluginLoader


def test_hook_order_and_isolation():
    bus = HookBus()
    seen: list[str] = []

    def first(payload):
        seen.append("first")
        raise RuntimeError("boom")

    def second(payload):
        seen.append("second")
        payload["args"] = {"x": 2}
        return payload

    bus.register("tool.execute.before", "a", first, priority=1)
    bus.register("tool.execute.before", "b", second, priority=2)
    out = asyncio.run(bus.emit("tool.execute.before", {"args": {"x": 1}}))
    assert seen == ["first", "second"]
    assert out["args"] == {"x": 2}


def test_disabled_owner_is_skipped():
    bus = HookBus()
    calls: list[str] = []
    bus.register("chat.message", "a", lambda payload: calls.append("a") or payload)
    bus.set_enabled("a", False)
    asyncio.run(bus.emit("chat.message", {}))
    assert calls == []


def test_loader_reads_manifest_and_hooks(tmp_path):
    (tmp_path / "demo.py").write_text(
        "PLUGIN_NAME='demo'\n"
        "PLUGIN_HOOKS={'chat.message': lambda payload: payload}\n"
        "def tool_echo(text=''):\n"
        "    return text\n",
        encoding="utf-8",
    )
    (tmp_path / "demo.json").write_text(
        '{"version": "2.0", "description": "manifest", "options": {"k": "v"}}',
        encoding="utf-8",
    )
    (tmp_path / "demo.enabled").touch()
    loader = PluginLoader(str(tmp_path))
    plugins = loader.load_all()
    assert plugins[0].version == "2.0"
    assert plugins[0].description == "manifest"
    assert plugins[0].options == {"k": "v"}
    assert "chat.message" in plugins[0].hooks
    assert loader.unload("demo")
    assert loader.get("demo") is None
