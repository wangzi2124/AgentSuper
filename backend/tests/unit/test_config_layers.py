"""Layered config merge and ``{env:}`` / ``{file:}`` substitution."""

from __future__ import annotations

from pathlib import Path

from config_layers import deep_merge, load_layered_config, substitute


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_deep_merge_recurses_and_overrides():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    override = {"nested": {"y": 3, "z": 4}, "b": 2}
    assert deep_merge(base, override) == {
        "a": 1,
        "nested": {"x": 1, "y": 3, "z": 4},
        "b": 2,
    }
    # The original must not be mutated.
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}}


def test_substitute_env_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSUPER_TEST_ENV", "hello")
    _write(tmp_path / "secret.txt", "s3cr3t\n")
    out = substitute(
        {
            "plain": "x",
            "env": "{env:AGENTSUPER_TEST_ENV}",
            "missing": "{env:AGENTSUPER_MISSING_ENV}",
            "file": "{file:secret.txt}",
            "nested": ["{env:AGENTSUPER_TEST_ENV}", 1],
        },
        tmp_path,
    )
    assert out["env"] == "hello"
    assert out["missing"] == ""
    assert out["file"] == "s3cr3t"
    assert out["nested"] == ["hello", 1]


def test_layered_precedence_and_nested_merge(tmp_path, monkeypatch):
    system = _write(tmp_path / "system.json", '{"a": "system", "n": {"x": 1}}')
    glob = _write(tmp_path / "global.json", '{"a": "global", "n": {"y": 2}}')
    project = _write(tmp_path / "project.json", '{"a": "project"}')
    app = _write(tmp_path / "app.json", '{"n": {"z": 3}}')

    import config_layers as mod

    monkeypatch.setattr(
        mod,
        "config_layers",
        lambda workspace=None: [
            ("system", system),
            ("global", glob),
            ("project", project),
            ("app", app),
        ],
    )
    merged = load_layered_config()
    assert merged["a"] == "project"
    assert merged["n"] == {"x": 1, "y": 2, "z": 3}


def test_layer_substitution_uses_own_directory(tmp_path, monkeypatch):
    project = _write(tmp_path / "project.json", '{"token": "{file:token.txt}"}')
    _write(tmp_path / "token.txt", "project-token")

    import config_layers as mod

    monkeypatch.setattr(
        mod, "config_layers", lambda workspace=None: [("project", project)]
    )
    assert load_layered_config()["token"] == "project-token"


def test_corrupt_layer_is_ignored(tmp_path, monkeypatch):
    good = _write(tmp_path / "good.json", '{"a": 1}')
    bad = _write(tmp_path / "bad.json", "{not json")

    import config_layers as mod

    monkeypatch.setattr(
        mod,
        "config_layers",
        lambda workspace=None: [("global", bad), ("app", good)],
    )
    assert load_layered_config() == {"a": 1}
