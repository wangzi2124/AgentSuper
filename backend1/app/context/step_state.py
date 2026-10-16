"""步骤状态落盘（[C5] 小步快走 · 方案 D 基础）。

长任务执行时把「已完成工作 + 涉及文件 + 待办」写成结构化 STEP_STATE 文件，
供下一请求/下一轮恢复：上下文只装「摘要 + 当前步」，旧原始轮次不再携带，
从根源上避免上下文随轮次线性膨胀（而非依赖压缩事后清理）。

文件位置：会话工作目录（opencode ctx.directory）下的 `.agents/steps/`。
每轮写一个 `<seq>.md`，另维护 `latest.md` 指向最新一份（接力续跑读它）。
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_STEP_DIR_NAME = ".agents"
_STEPS_SUBDIR = "steps"


def step_state_dir(session_dir: str) -> Path | None:
    """返回会话工作目录下的步骤状态目录；无会话目录时返回 None（不落盘）。"""
    if not session_dir:
        return None
    try:
        d = Path(session_dir) / _STEP_DIR_NAME / _STEPS_SUBDIR
        return d
    except Exception:  # noqa: BLE001
        return None


def write_step_state(session_dir: str, seq: int, state: dict) -> str | None:
    """把一份步骤状态落盘（`<seq>.md` + 更新 `latest.md`），返回文件路径。

    state 建议字段（对齐 COMPACTION_TEMPLATE）：objective / completed /
    active / blocked / next_move / files（涉及文件路径清单）。
    全部存为 Markdown 正文，latest.md 用 JSON 记录元信息。
    """
    d = step_state_dir(session_dir)
    if d is None:
        return None
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{seq:04d}.md"
        body = _render_state(state)
        path.write_text(body, encoding="utf-8")
        (d / "latest.md").write_text(
            json.dumps({
                "seq": int(seq),
                "ts": round(time.time(), 3),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except Exception as e:  # noqa: BLE001 —— 落盘失败不阻断执行
        logger.warning("write_step_state failed: %s", e)
        return None


def load_latest_step_state(session_dir: str) -> tuple[int | None, str | None]:
    """读取最新一份步骤状态，返回 (seq, 正文)；无则 (None, None)。"""
    d = step_state_dir(session_dir)
    if d is None:
        return None, None
    try:
        meta = d / "latest.md"
        if not meta.exists():
            return None, None
        data = json.loads(meta.read_text(encoding="utf-8"))
        seq = int(data.get("seq", 0) or 0)
        body = (d / f"{seq:04d}.md").read_text(encoding="utf-8")
        return seq, body
    except Exception:  # noqa: BLE001
        return None, None


def _render_state(state: dict) -> str:
    """把状态 dict 渲染成 Markdown（供模型在下一请求读回）。"""
    def _sec(title: str, key: str) -> str:
        items = state.get(key)
        if isinstance(items, str):
            items = [items]
        items = items or []
        lines = "\n".join(f"- {i}" for i in items if str(i).strip())
        return f"## {title}\n{lines if lines else '- (none)'}"

    obj = state.get("objective") or ""
    parts = [
        f"# Task Step State\n\n## Objective\n{obj}" if obj else "# Task Step State",
        _sec("Completed", "completed"),
        _sec("Active", "active"),
        _sec("Blocked", "blocked"),
        _sec("Next Move", "next_move"),
        _sec("Relevant Files", "files"),
    ]
    return "\n\n".join(parts)
