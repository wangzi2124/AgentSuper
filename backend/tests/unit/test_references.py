"""G-07: named references (local + cached git status)."""

from __future__ import annotations

import asyncio

from references import (
    selection_text,
    ReferenceRegistry,
    ReferenceSpec,
    delete_reference,
    load_specs,
    reference_instructions,
    write_reference,
)


def test_local_reference_ready(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")
    registry = ReferenceRegistry(
        {"local": ReferenceSpec(name="local", path=str(target), description="d")}
    )
    state = registry.states["local"]
    assert state.status == "ready"
    assert registry.tree("local") == ["a.txt"]
    write_reference(
        "local", {"path": str(target), "description": "d"}, workspace=tmp_path, scope="project"
    )
    assert "local: d" in reference_instructions(tmp_path)


def test_missing_local_reference_errors(tmp_path):
    registry = ReferenceRegistry(
        {"gone": ReferenceSpec(name="gone", path=str(tmp_path / "nope"))}
    )
    assert registry.states["gone"].status == "error"


def test_git_reference_prepares_async(tmp_path, monkeypatch):
    spec = ReferenceSpec(name="gitref", repository="https://example.com/x.git", branch="main")
    registry = ReferenceRegistry({"gitref": spec})
    state = registry.states["gitref"]
    assert state.status == "missing"

    def fake_clone(state):
        from pathlib import Path

        root = Path(state.root)
        root.mkdir(parents=True, exist_ok=True)
        (root / "README.md").write_text("hi", encoding="utf-8")

    monkeypatch.setattr(registry, "_clone", fake_clone)

    async def run():
        registry.ensure("gitref")
        for _ in range(100):
            if registry.states["gitref"].status != "preparing":
                break
            await asyncio.sleep(0.01)
        return registry.states["gitref"]

    state = asyncio.run(run())
    assert state.status == "ready"
    assert registry.tree("gitref") == ["README.md"]


def test_reference_store_crud(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_reference(
        "docs",
        {"path": str(workspace), "description": "d"},
        workspace=workspace,
        scope="project",
    )
    specs = load_specs(workspace)
    assert specs["docs"].source == "project"
    assert delete_reference("docs", workspace=workspace, scope="project")
    assert "docs" not in load_specs(workspace)


def test_selection_text_lists_root(tmp_path):
    target = tmp_path / "ref"
    target.mkdir()
    (target / "a.txt").write_text("x", encoding="utf-8")
    write_reference("docs", {"path": str(target)}, workspace=tmp_path, scope="project")
    text = selection_text(tmp_path, ["docs"])
    assert str(target) in text and "a.txt" in text
