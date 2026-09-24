"""轮次级快照（turn）——外部文件 before 归档 + 整轮「撤回改动」恢复。

一个 chat 请求 = 一个 turn，期间维护活跃 TurnSnapshot：
- worktree（git 仓库）内、未被忽略的文件：前后各拍一次 tree，before_tree 负责整轮恢复
  （走 Snapshot.revert）。忽略文件（如 node_modules/.venv/data/**）git 拍不到，
  视同外部文件用内容归档兜底。
- 外部文件（worktree 之外，如用户桌面的 C:\\...\\Untitled-1.py）：文件工具首次写入前
  调 archive_external() 归档「修改前内容 / 缺席」标记；恢复时写回或删除。

活跃 turn 经 contextvar 传播：asyncio task 天然继承，AgentBus / LangGraph 的子 task
都在同一请求链路上，因此 file_tools 写外部文件时能取到当前 turn 做归档；并发请求
各自独立，互不串扰。

恢复语义（每轮消息关联一个 descriptor）：
  POST /api/chat/multi-agent/restore-snapshot {conversation_id, message_id}
  → 内部文件从 before_tree 恢复；外部文件写回归档内容 / 删除（原来不存在则删）。
"""
from __future__ import annotations

import difflib
import logging
import os
import shutil
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .snapshot import Patch, Snapshot, _default_data_dir, _sha1

logger = logging.getLogger(__name__)

TURNS_ROOT_REL = "snapshot/turns"

# 外部文件归档体积上限：超过则跳过（巨大二进制文件恢复成本高于收益，放弃还原）。
MAX_EXTERNAL_ARCHIVE_BYTES = 20 * 1024 * 1024

_current: ContextVar[Optional["TurnSnapshot"]] = ContextVar(
    "snapshot_active_turn", default=None
)


@dataclass
class ExternalRec:
    """外部文件的一笔归档记录：path 绝对路径 + 修改前状态。"""

    path: str
    before: str  # "present"（有 before 内容）| "absent"（turn 前不存在）
    blob: Optional[str] = None  # 相对 data_dir 的 blob 路径（before=present 时有值）


class TurnSnapshot:
    """单个 chat 轮次的快照上下文（git 树 + 外部内容归档）。"""

    def __init__(self, snap: Snapshot, turn_id: str) -> None:
        self.snap = snap
        self.turn_id = turn_id
        self.before_tree: str = ""
        self._lock = threading.Lock()
        self.external: dict[str, ExternalRec] = {}

    # ── 目录布局 ──────────────────────────────────────────────────────────

    def _base_dir(self) -> Path:
        return self.snap.data_dir or _default_data_dir()

    def _worktree_key(self) -> str:
        return _sha1(str(self.snap.worktree).encode("utf-8"))

    def _external_dir(self) -> Path:
        return self._base_dir() / TURNS_ROOT_REL / self._worktree_key() / self.turn_id / "external"

    def _blob_abs(self, path: Path) -> Path:
        return self._external_dir() / f"{_sha1(path.as_posix())}.bin"

    # ── git 前后树 ────────────────────────────────────────────────────────

    def capture_before(self) -> str:
        """请求开始时拍 before tree；非 git 项目返回 ''（外部归档仍可用）。"""
        try:
            self.before_tree = self.snap.track() or ""
        except Exception:
            logger.warning("turn before-tree failed", exc_info=True)
            self.before_tree = ""
        return self.before_tree

    # ── 外部文件判定与归档 ────────────────────────────────────────────────

    def _in_worktree(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.snap.worktree.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _is_git_ignored(self, rel: str) -> bool:
        """worktree 内文件是否会被 git 快照忽略（ignored → 归档兜底）。"""
        try:
            proc = self.snap._git(
                ["check-ignore", "--no-index", "-q", rel], check=False, timeout=10
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _archive_eligible(self, path: Path) -> bool:
        """是否需要内容归档：worktree 外 或 worktree 内但 git 拍不到（忽略 / 非 git 项目）。"""
        worktree = self.snap.worktree.resolve()
        try:
            rel = path.resolve().relative_to(worktree).as_posix()
        except (ValueError, OSError):
            return True
        if not self.snap.enabled():
            return True
        return self._is_git_ignored(rel)

    def archive(self, path: Path) -> None:
        """归档某个路径的「修改前状态」（幂等：每 turn 每路径只归档一次）。

        由 file_tools 写路径在变更前调用；归档失败/延迟只影响恢复能力，绝不影响写入。
        """
        p = path.resolve()
        key = os.fspath(p)
        with self._lock:
            if key in self.external:
                return
        try:
            eligible = self._archive_eligible(p)
        except Exception:
            logger.debug("snapshot archive eligibility failed for %s", p, exc_info=True)
            return
        if not eligible:
            return
        if not p.is_file():
            with self._lock:
                self.external.setdefault(key, ExternalRec(path=key, before="absent"))
            return
        try:
            if p.stat().st_size > MAX_EXTERNAL_ARCHIVE_BYTES:
                logger.warning("snapshot archive skipped (too large): %s", p)
                return
            data = p.read_bytes()
        except OSError:
            return
        try:
            blob_abs = self._blob_abs(p)
            blob_abs.parent.mkdir(parents=True, exist_ok=True)
            # 直接写内容寻址 blob（名字=内容 sha1，重复写同内容无害）。
            # 不用「.tmp + replace」原子舞步：Windows 上新建深层目录后立即写 .tmp
            # 偶发 FileNotFoundError（疑似杀软/索引瞬时扫描），而直写同名 blob 稳定。
            blob_abs.write_bytes(data)
            rel = blob_abs.relative_to(self._base_dir()).as_posix()
        except OSError:
            logger.warning("snapshot archive write failed for %s", p, exc_info=True)
            return
        with self._lock:
            self.external.setdefault(
                key, ExternalRec(path=key, before="present", blob=rel)
            )

    def _blob_bytes(self, rec: ExternalRec) -> bytes:
        try:
            return (self._base_dir() / (rec.blob or "")).read_bytes()
        except OSError:
            return b""

    # ── 汇总：files_changed + 恢复 descriptor ─────────────────────────────

    def finalize(self, git_entries: list[dict]) -> tuple[list[dict], dict]:
        """合并 git(内部) 与归档(外部) 变更，返回 (files_changed, restore descriptor)。"""
        external_recs: list[dict] = []
        external_entries: list[dict] = []
        for key in sorted(self.external):
            rec = self.external[key]
            cur = None
            try:
                p = Path(rec.path)
                cur = p.read_bytes() if p.is_file() else None
            except OSError:
                pass
            if rec.before == "absent":
                if cur is None:
                    continue
                status, before_bytes = "added", b""
            else:
                before_bytes = self._blob_bytes(rec)
                if cur is None:
                    status = "deleted"
                elif before_bytes == cur:
                    continue  # 写了同内容：视为无改动，不占展示位
                else:
                    status = "modified"
            add, delete, binary = _line_stats(before_bytes, cur or b"")
            external_entries.append({
                "file": rec.path,
                "status": status,
                "binary": binary,
                "additions": add,
                "deletions": delete,
                "external": True,
            })
            external_recs.append({"path": rec.path, "before": rec.before, "blob": rec.blob})
        descriptor = {
            "worktree": str(self.snap.worktree),
            "before_tree": self.before_tree,
            "internal": [e["file"] for e in git_entries],
            "external": external_recs,
        }
        return [*git_entries, *external_entries], descriptor


def _line_stats(before: bytes, after: bytes) -> tuple[int, int, bool]:
    """统计行增/删与二进制标记（非 utf-8 文本视为二进制，不数行）。"""
    try:
        b_lines = before.decode("utf-8").splitlines()
        a_lines = after.decode("utf-8").splitlines()
    except (UnicodeDecodeError, ValueError):
        return 0, 0, True
    add = delete = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b_lines, a_lines).get_opcodes():
        if tag in ("replace", "delete"):
            delete += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
    return add, delete, False


# ── contextvar 门面（供 chatmod / file_tools 调用）─────────────────────────


def start_turn(snap: Snapshot, turn_id: str | None = None) -> "TurnSnapshot":
    """开启新 turn 并置为当前活跃；返回 turn（调用方再 capture_before）。"""
    turn = TurnSnapshot(snap, turn_id or uuid.uuid4().hex)
    _current.set(turn)
    return turn


def active_turn() -> Optional["TurnSnapshot"]:
    return _current.get()


def end_turn() -> None:
    _current.set(None)


def archive_external(path: str | Path) -> None:
    """file_tools 写外部文件前调用：归档当前 turn 中该路径的 before 状态。"""
    turn = _current.get()
    if turn is None:
        return
    try:
        turn.archive(Path(path))
    except Exception:
        logger.debug("snapshot archive_external failed for %s", path, exc_info=True)


# ── 恢复 ───────────────────────────────────────────────────────────────────


def restore_session_turn(snap: Snapshot | None, descriptor: dict) -> dict:
    """按 descriptor 恢复一轮改动：内部文件走 before_tree，外部文件写回/删除。

    返回 {"internal": n, "external": n, "restored": [path...], "missing": [blob...]}。
    安全降级：任一文件失败只跳过该文件，不中断整体恢复。
    """
    if not descriptor:
        return {"internal": 0, "external": 0, "restored": [], "missing": []}
    restored: list[str] = []
    missing: list[str] = []
    internal_n = external_n = 0

    worktree = Path(descriptor.get("worktree") or "")
    before_tree = descriptor.get("before_tree") or ""
    internal = descriptor.get("internal") or []
    if snap is not None and before_tree and internal and worktree:
        try:
            abs_items = [
                os.fspath((worktree / rel).resolve())
                for rel in internal
            ]
            snap.revert([Patch(before_tree, abs_items)])
            restored.extend(internal)
            internal_n = len(internal)
        except Exception:
            logger.warning("snapshot internal restore failed", exc_info=True)

    base = (snap.data_dir or _default_data_dir()) if snap is not None else _default_data_dir()
    for ext in descriptor.get("external") or []:
        path = Path(ext.get("path") or "")
        if not path:
            continue
        try:
            if ext.get("before") == "present" and ext.get("blob"):
                blob_abs = base / ext["blob"]
                if not blob_abs.is_file():
                    missing.append(ext["blob"])
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob_abs.read_bytes())
            else:
                # before=absent → turn 后新增的文件，删掉即还原
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink(missing_ok=True)
            restored.append(str(path))
            external_n += 1
        except OSError as e:
            logger.warning("snapshot external restore failed: %s (%s)", path, e)
    return {"internal": internal_n, "external": external_n, "restored": restored, "missing": missing}


def cleanup_turn_archives(ttl_days: int = 7, data_dir: Path | None = None) -> None:
    """清理过期轮次级外部归档目录（mtime 早于 ttl_days 天即删除）。

    data_dir 指向快照数据根（缺省取默认 data/）；测试可传 tmp data_dir。
    """
    root = (data_dir or _default_data_dir()) / TURNS_ROOT_REL
    if not root.exists():
        return
    cutoff = time.time() - ttl_days * 86400
    try:
        for wh in root.iterdir():
            if not wh.is_dir():
                continue
            for tid in wh.iterdir():
                try:
                    if tid.stat().st_mtime < cutoff:
                        shutil.rmtree(tid, ignore_errors=True)
                except OSError:
                    continue
    except OSError:
        logger.exception("snapshot turn archives cleanup failed")


__all__ = [
    "MAX_EXTERNAL_ARCHIVE_BYTES",
    "TURNS_ROOT_REL",
    "TurnSnapshot",
    "active_turn",
    "archive_external",
    "cleanup_turn_archives",
    "end_turn",
    "restore_session_turn",
    "start_turn",
]