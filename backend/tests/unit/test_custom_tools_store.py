"""Unit tests for the script-tool store API shape."""

from __future__ import annotations

from app.skills.custom_tools import CustomToolStore

_SCRIPT = 'def tool_echo(msg: str) -> str:\n    return "echo:" + msg\n'


def test_create_script_returns_unified_custom_tool_item(tmp_path):
    store = CustomToolStore(plugins_dir=str(tmp_path / "plugins"))
    item = store.create_script("echo", "echo tool", _SCRIPT, enabled=True)
    assert item["name"] == "echo"
    assert item["type"] == "script"
    assert item["tools"] == ["tool_echo"]
    assert item["description"] == "echo tool"
    assert item["enabled"] is True
    # list/get shape must match create response
    assert store.get("echo") == item
    assert len(store.list()) == 1


def test_toggle_and_remove_script(tmp_path):
    store = CustomToolStore(plugins_dir=str(tmp_path / "plugins"))
    store.create_script("echo", "echo tool", _SCRIPT, enabled=True)
    assert store.toggle("echo", False) is True
    assert store.get("echo")["enabled"] is False
    assert store.toggle("echo", True) is True
    assert store.get("echo")["enabled"] is True
    assert store.remove("echo") is True
    assert store.get("echo") is None
    assert store.toggle("echo", False) is False
