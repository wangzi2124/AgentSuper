"""Unit tests for the custom-tool store API shape (pin items)."""

from __future__ import annotations

import pytest

from app.skills.custom_tools import CustomToolStore


def test_create_pin_returns_unified_custom_tool_item(tmp_path):
    store = CustomToolStore(
        plugins_dir=str(tmp_path / "plugins"),
        pinned_path=str(tmp_path / "pinned_tools.json"),
    )
    item = store.create_pin("read_file", "pin read_file")
    assert item["name"] == "read_file"
    assert item["type"] == "pin"
    assert item["tools"] == ["read_file"]
    assert item["description"] == "pin read_file"
    assert item["enabled"] is True
    assert store.pinned_tools() == ["read_file"]
    # The list/get shape must match the create response.
    assert store.get("read_file")["name"] == "read_file"


def test_duplicate_pin_is_rejected(tmp_path):
    store = CustomToolStore(
        plugins_dir=str(tmp_path / "plugins"),
        pinned_path=str(tmp_path / "pinned_tools.json"),
    )
    store.create_pin("read_file")
    with pytest.raises(ValueError):
        store.create_pin("read_file")
