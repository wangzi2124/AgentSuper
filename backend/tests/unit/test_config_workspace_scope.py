"""Workspace-scoped project config (contextvar + get_settings)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from config import bind_workspace_settings, get_settings, settings


def _isolate_layers(monkeypatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    monkeypatch.setattr("config_layers.system_config_path", lambda: empty)
    monkeypatch.setattr("config_layers.global_config_path", lambda: empty)
    monkeypatch.setattr("config_layers.app_config_path", lambda: empty)


def _project(workspace: Path, values: dict) -> Path:
    path = workspace / ".agentsuper" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def test_project_config_applies_and_restores(tmp_path, monkeypatch):
    _isolate_layers(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _project(workspace, {"llm_model": "scope/model", "tool_concurrency": 7})
    original = settings.llm_model

    with bind_workspace_settings(workspace) as scoped:
        assert get_settings() is scoped
        assert scoped.llm_model == "scope/model"
        assert scoped.tool_concurrency == 7

    assert get_settings() is settings
    assert settings.llm_model == original


def test_environment_wins_over_project(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _project(workspace, {"llm_model": "scope/model"})
    monkeypatch.setenv("LLM_MODEL", "env/model")

    with bind_workspace_settings(workspace) as scoped:
        assert scoped.llm_model == "env/model"


def test_no_project_config_keeps_global_object(tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()

    with bind_workspace_settings(workspace) as scoped:
        assert scoped is settings


def test_project_cannot_override_process_paths(tmp_path, monkeypatch):
    _isolate_layers(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _project(
        workspace,
        {
            "llm_model": "scope/model",
            "agentsuper_data": str(tmp_path / "evil"),
            "permissions": [{"action": "allow", "pattern": "*"}],
        },
    )

    with bind_workspace_settings(workspace) as scoped:
        assert scoped.llm_model == "scope/model"
        assert scoped.agentsuper_data == settings.agentsuper_data
        assert scoped.permissions == settings.permissions


@pytest.mark.asyncio
async def test_concurrent_workspaces_are_isolated(tmp_path, monkeypatch):
    _isolate_layers(monkeypatch, tmp_path)
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    _project(ws_a, {"llm_model": "scope/a"})
    _project(ws_b, {"llm_model": "scope/b"})

    async def read_model(workspace: Path) -> str:
        with bind_workspace_settings(workspace):
            await asyncio.sleep(0.01)
            return get_settings().llm_model

    assert await asyncio.gather(read_model(ws_a), read_model(ws_b)) == [
        "scope/a",
        "scope/b",
    ]
    assert get_settings() is settings


@pytest.mark.asyncio
async def test_async_generator_scope_is_released(tmp_path, monkeypatch):
    _isolate_layers(monkeypatch, tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _project(workspace, {"llm_model": "scope/model"})

    async def stream_models():
        with bind_workspace_settings(workspace):
            yield get_settings().llm_model
            yield get_settings().llm_model

    stream = stream_models()
    assert await stream.__anext__() == "scope/model"
    assert await stream.__anext__() == "scope/model"
    await stream.aclose()

    assert get_settings() is settings
