"""Design → matrix edge traceability gate (design §11.10)."""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[3] / "docs"
MATRIX = DOCS / "harness-design-gap-matrix.md"

EXPECTED_EDGES = {
    f"E{i:02d}"
    for i in (
        list(range(1, 16))
        + list(range(20, 28))
        + list(range(30, 36))
        + list(range(40, 43))
        + list(range(50, 54))
        + list(range(60, 67))
        + list(range(70, 78))
    )
}


def test_all_design_edges_are_in_the_frozen_matrix():
    text = MATRIX.read_text(encoding="utf-8")
    found = set(re.findall(r"\bE\d{2}\b", text))
    assert not (EXPECTED_EDGES - found), f"missing edges: {sorted(EXPECTED_EDGES - found)}"


def test_invariants_are_listed():
    text = MATRIX.read_text(encoding="utf-8")
    for i in range(1, 9):
        assert f"I-SW{i}" in text
