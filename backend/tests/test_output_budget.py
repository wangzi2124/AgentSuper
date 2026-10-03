"""输出上限调大（2026-10-03）的回归测试。

事故背景：写俄罗斯方块时，模型把整份代码塞进单次 `tool_write_file` 参数（24,528 字符），
正好撞 8_192 输出上限被腰斩。这里锁死三件容易再次悄悄退化的事：

1. **模型目录才是真天花板** —— `resolve_max_output_tokens` 取 `min(模型声明, 全局)`
   且绝不放大。只改 `LLM_MAX_TOKENS` 而不动目录声明，等于什么都没做（表现为
   「配置改了却依然每次撞 8192」）。
2. **输出预留必须 ≥ 输出上限** —— 否则模型一次输出就能吃光 usable，历史窗口归零。
3. **`_seed_builtin` 必须真的刷新物化行** —— `build_catalog()` 让 DB 行优先于常量，
   陈旧的 builtin 行会永久遮蔽更新后的常量（改代码对已有安装完全无效）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models import catalog  # noqa: E402
from app.models.builtin_catalog import BUILTIN_CATALOG  # noqa: E402

DEEPSEEK = "deepseek/deepseek-v4-flash"


# ── 1. 模型目录声明（真正的天花板）─────────────────────────────────

def test_builtin_deepseek_output_cap_raised():
    """内建常量里的 deepseek 声明必须 ≥16_384，否则全局值被 min() 压回 8_192。"""
    entry = next(e for e in BUILTIN_CATALOG if e["id"] == DEEPSEEK)
    assert entry["limits"]["max_output_tokens"] >= 16_384, entry["limits"]


def test_resolve_max_output_tokens_actually_reaches_16384():
    """端到端：配置 + 目录共同决定的结果必须是 16_384。"""
    assert settings.llm_max_tokens >= 16_384
    assert catalog.resolve_max_output_tokens(DEEPSEEK, settings.llm_max_tokens) == 16_384


def test_resolve_never_widens_beyond_catalog_declaration():
    """守住「取小」语义：全局给再大也不能超过模型声明（本次事故的反面）。"""
    entry = next(e for e in BUILTIN_CATALOG if e["id"] == DEEPSEEK)
    declared = entry["limits"]["max_output_tokens"]
    assert catalog.resolve_max_output_tokens(DEEPSEEK, declared * 10) == declared


# ── 2. 输出预留 ≥ 输出上限（自洽性）───────────────────────────────

def test_reserve_is_not_less_than_output_cap():
    """预留 < 输出上限是自相矛盾的：一次输出就能吃光 usable，历史窗口归零。"""
    assert settings.context_reserve_tokens >= settings.llm_max_tokens, (
        f"CONTEXT_RESERVE_TOKENS({settings.context_reserve_tokens}) < "
        f"LLM_MAX_TOKENS({settings.llm_max_tokens})"
    )


def test_usable_context_not_shrunk_by_the_raise():
    """抬高输出不能牺牲历史窗口。

    调前 usable = 24000 - 8192 = 15808。这次把 MAX_CONTEXT_TOKENS 一并提到 32768
    正是为了不缩水（32768-16384=16384）。若哪天只调 LLM_MAX_TOKENS 而忘了同步抬高
    MAX_CONTEXT_TOKENS，usable 会掉到 7616 —— 比调前更差，且不易察觉。
    """
    usable = settings.max_context_tokens - settings.context_reserve_tokens
    assert usable >= 15_808, f"usable 缩水到 {usable}（调前 15808）"


def test_context_budget_modules_see_the_new_budget():
    """budget 模块的实际取值要跟着走，不能还停在旧的 15808。"""
    from app.context.budget import compaction_threshold_tokens, usable_context_tokens

    usable = usable_context_tokens(DEEPSEEK)
    assert usable >= 15_808, usable
    # 压缩必须早于截断发生
    assert 0 < compaction_threshold_tokens(DEEPSEEK) < usable


# ── 3. _seed_builtin 必须刷新物化行 ──────────────────────────────────

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE catalog_entries (id TEXT PRIMARY KEY, kind TEXT, data TEXT)")
    return conn


def test_seed_builtin_inserts_missing_rows():
    from app.models.catalog_db import _seed_builtin

    conn = _fresh_conn()
    _seed_builtin(conn)
    n = conn.execute("SELECT COUNT(*) FROM catalog_entries WHERE kind='builtin'").fetchone()[0]
    assert n == len(BUILTIN_CATALOG)


def test_seed_builtin_refreshes_stale_materialized_row():
    """陈旧的 builtin 行必须被常量刷新 —— 否则改 builtin_catalog.py 对已有安装无效。"""
    from app.models.catalog_db import _seed_builtin

    stale = json.dumps({"id": DEEPSEEK, "limits": {"max_output_tokens": 8192}}, ensure_ascii=False)
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO catalog_entries(id, kind, data) VALUES (?, 'builtin', ?)",
        (DEEPSEEK, stale),
    )
    _seed_builtin(conn)

    row = conn.execute(
        "SELECT data FROM catalog_entries WHERE id = ? AND kind='builtin'", (DEEPSEEK,)
    ).fetchone()
    got = json.loads(row[0])
    assert got["limits"]["max_output_tokens"] >= 16_384, "陈旧物化行没被刷新"
    assert got["context_length"] == 160000


def test_seed_builtin_is_idempotent_no_write_amplification():
    """数据一致时不得重复写（每次启动都无条件 UPDATE 会白白磨损 DB）。"""
    from app.models.catalog_db import _seed_builtin

    conn = _fresh_conn()
    _seed_builtin(conn)
    before = conn.total_changes
    _seed_builtin(conn)
    assert conn.total_changes == before, "一致时仍发生了写入"


@pytest.mark.parametrize("kind", ["override", "extra"])
def test_seed_builtin_never_touches_user_rows(kind):
    """用户的 override/extra 行绝不能被内建常量覆盖。"""
    from app.models.catalog_db import _seed_builtin

    mine = json.dumps({"id": DEEPSEEK, "limits": {"max_output_tokens": 4096},
                       "my": "custom"}, ensure_ascii=False)
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO catalog_entries(id, kind, data) VALUES (?, ?, ?)",
        (DEEPSEEK, kind, mine),
    )
    _seed_builtin(conn)

    row = conn.execute("SELECT kind, data FROM catalog_entries WHERE id = ?", (DEEPSEEK,)).fetchone()
    assert row[0] == kind, "用户行被内建常量替换了"
    assert json.loads(row[1])["my"] == "custom"
    assert json.loads(row[1])["limits"]["max_output_tokens"] == 4096