"""Built-in formatter table (opencode gap G-03).

Only executable names and extensions are declared here; there are no
vendor-specific branches. If an executable is missing the entry is skipped.
"""

from __future__ import annotations

from .spec import FormatterSpec

BUILTIN_FORMATTERS: tuple[FormatterSpec, ...] = (
    FormatterSpec(
        name="ruff",
        command=("ruff", "format"),
        extensions=(".py", ".pyi"),
        requires=("ruff",),
    ),
    FormatterSpec(
        name="gofmt",
        command=("gofmt", "-w"),
        extensions=(".go",),
        requires=("gofmt",),
    ),
    FormatterSpec(
        name="rustfmt",
        command=("rustfmt",),
        extensions=(".rs",),
        requires=("rustfmt",),
    ),
    FormatterSpec(
        name="prettier",
        command=("prettier", "--write"),
        extensions=(
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".json",
            ".jsonc",
            ".css",
            ".scss",
            ".md",
            ".yaml",
            ".yml",
            ".html",
        ),
        requires=("prettier",),
    ),
    FormatterSpec(
        name="biome",
        command=("biome", "format", "--write"),
        extensions=(".js", ".jsx", ".ts", ".tsx", ".json", ".jsonc"),
        requires=("biome",),
    ),
)

__all__ = ["BUILTIN_FORMATTERS"]
