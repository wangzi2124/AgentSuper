"""
Snapshot — 快照与回滚层(规格模块 B)。

对齐 `opencode-构建规格.md` §5:
  - 数据布局:<Global.Path.data>/snapshot/<projectId>/<sha1(worktree)>/  (内嵌 git 仓库)
  - projectId = git 根提交 SHA(`git rev-list --max-parents=0 HEAD`)
  - worktree-hash = 工作区绝对路径的 sha1
  - init / add / track / patch / diff / diffFull / restore / revert / cleanup
  - enabled() = (worktree 是 git 项目) && (not disabled);否者全部 no-op
  - 每 gitdir 一把信号量,全部操作串行于自身仓库
  - 快照零干扰:只用 git 内部对象(write-tree),不建 commit、不动用户的 index/分支/HEAD

本模块不参与 agent 主循环的"步骤级"粘合(§7.4 后续里程碑);对外提供独立可测接口,
调用方(会话服务 / 工具层)可自行决定何时 track / revert。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BIG_FILE_THRESHOLD = 2 * 1024 * 1024  # 2 MiB(规格 §5.4)
GC_TIMEOUT = 300


class SnapshotError(RuntimeError):
    """快照操作失败(底层 git 命令返回非零)。"""


@dataclass
class Patch:
    """一批变更:某 step 的 tree hash 及其 changed files。规格 §5.2 Patch。"""

    hash: str
    files: list[str]  # 绝对路径

    def to_dict(self) -> dict:
        return {"hash": self.hash, "files": list(self.files)}


@dataclass
class FileDiff:
    """单个文件的完整 diff。规格 §5.2 FileDiff。"""

    file: str
    status: str  # "added" | "deleted" | "modified"
    binary: bool = False
    additions: int = 0
    deletions: int = 0
    before: Optional[str] = None
    after: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "status": self.status,
            "binary": self.binary,
            "additions": self.additions,
            "deletions": self.deletions,
            "before": self.before,
            "after": self.after,
        }


def _sha1(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()


def _default_data_dir() -> Path:
    """默认 <data> 目录 = backend/data(对齐 runtime 的 AGENTSUPER_DATA,缺省同址)。"""
    try:
        from app.storage.paths import global_paths

        return global_paths()["data"]
    except Exception:
        return Path(__file__).resolve().parents[2] / "data"


def _project_id(worktree: Path) -> str:
    """项目 ID:优先 git 根提交 SHA(规格 §5.1),无提交时回退为路径 sha1。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass
    return _sha1(str(worktree).encode("utf-8"))


class Snapshot:
    """基于内部 git 仓库的快照/回滚服务(规格模块 B)。"""

    def __init__(
        self,
        worktree: str | Path,
        data_dir: str | Path | None = None,
        git_binary: str = "git",
        disabled: bool = False,
        big_file_threshold: int = DEFAULT_BIG_FILE_THRESHOLD,
    ) -> None:
        self.worktree = Path(worktree).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.git = git_binary
        self.disabled = disabled
        self.big_file_threshold = big_file_threshold
        self._lock_map: dict[str, threading.Lock] = {}
        self._lock_guard = threading.Lock()
        self._project_id: Optional[str] = None
        self._gitdir_path: Optional[Path] = None

    # ── 门控与布局 ────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        """enabled() = (project.vcs === "git") && (config.snapshot !== false)。

        非 git 项目或显式关闭时,所有快照操作 no-op(规格 §5.2 gate)。
        """
        if self.disabled:
            return False
        if shutil.which(self.git) is None:
            return False
        try:
            return (self.worktree / ".git").exists()
        except OSError:
            return False

    def gitdir(self) -> Path:
        """快照 gitdir 路径:<data>/snapshot/<projectId>/<sha1(worktree)>/。

        只计算路径,不创建目录 —— 目录由首次 track 的 init 序列惰性创建,
        以便 `track()` 判断「existed」决定是否执行 §5.3 初始化。
        """
        if self._gitdir_path is not None:
            return self._gitdir_path
        pid = self._project_id
        if pid is None:
            pid = _project_id(self.worktree)
            self._project_id = pid
        base = self.data_dir or _default_data_dir()
        self._gitdir_path = (
            base / "snapshot" / pid / _sha1(str(self.worktree).encode("utf-8"))
        )
        return self._gitdir_path

    # ── git 子进程 ────────────────────────────────────────────────────────

    def _git(
        self,
        args: list[str],
        *,
        check: bool = True,
        cwd: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess:
        gd = self.gitdir()
        cmd = [self.git, "--git-dir", str(gd), "--work-tree", str(self.worktree)]
        cmd += args
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            cmd, cwd=cwd or self.worktree, env=env,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise SnapshotError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    def _git_only(
        self, args: list[str], *, check: bool = True, timeout: int = 60
    ) -> subprocess.CompletedProcess:
        """仅针对 gitdir 的命令(不需要 work-tree,如 config/gc)。"""
        gd = self.gitdir()
        cmd = [self.git, "--git-dir", str(gd)] + args
        proc = subprocess.run(
            cmd, cwd=self.worktree, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise SnapshotError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    def _lock(self) -> "threading.Lock":
        key = str(self.gitdir())
        with self._lock_guard:
            return self._lock_map.setdefault(key, threading.Lock())

    # ── 初始化 / 收集 ─────────────────────────────────────────────────────

    def _init_gitdir(self) -> None:
        """首次 track 惰性初始化:规格 §5.3 全套 config。"""
        gd = self.gitdir()
        gd.mkdir(parents=True, exist_ok=True)
        env = {"GIT_DIR": str(gd), "GIT_WORK_TREE": str(self.worktree)}
        proc = subprocess.run(
            ["git", "init"], cwd=self.worktree, env={**os.environ, **env},
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise SnapshotError(f"git init failed ({proc.returncode}): {proc.stderr.strip()}")
        for key, value in [
            ("core.autocrlf", "false"),
            ("core.longpaths", "true"),
            ("core.symlinks", "true"),
            ("core.fsmonitor", "false"),
            ("feature.manyFiles", "true"),
            ("index.version", "4"),
            ("index.threads", "true"),
            ("core.untrackedCache", "true"),
        ]:
            self._git_only(["config", key, value])

    def _sync_exclude(self, extra_big: list[str]) -> None:
        """把新跳过的大文件(仓库相对 POSIX 路径)并入 gitdir info/exclude。

        info/exclude 属于 git 的 exclude 层,`ls-files --exclude-standard` 与
        `git add` 均会尊重 —— 大文件只写一次,后续 add 不再反复触碰。
        """
        exclude = Path(self.gitdir()) / "info" / "exclude"
        existing: list[str] = []
        if exclude.exists():
            try:
                existing = exclude.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                existing = []
        auth = set(existing)
        added = [p for p in extra_big if f"/{p}" not in auth]
        if not added:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join([*existing, *[f"/{p}" for p in added]])
        try:
            exclude.write_text(payload + "\n" if payload else "", encoding="utf-8")
        except OSError as e:
            logger.warning("cannot update snapshot info/exclude: %s", e)

    def _add(self) -> None:
        """收集变更并入暂存。规格 §5.4 add() 演算法。"""
        if not self.enabled():
            return
        tracked = self._git(
            ["diff-files", "--name-only", "-z", "--", "."], check=False
        )
        untracked = self._git(
            ["ls-files", "--others", "--exclude-standard", "-z", "--", "."],
            check=False,
        )

        def _split(out: str) -> list[str]:
            return [p for p in out.split("\0") if p]

        all_paths: list[str] = []
        seen: set[str] = set()
        for p in [*_split(tracked.stdout), *_split(untracked.stdout)]:
            if p and p not in seen:
                seen.add(p)
                all_paths.append(p)
        if not all_paths:
            return

        big_files: list[str] = []
        for rel in all_paths:
            try:
                if (self.worktree / rel).stat().st_size > self.big_file_threshold:
                    big_files.append(rel)
            except OSError:
                continue  # 文件被删期间 stat 失败 → 跳过该项,不中断(规格边界)
        if big_files:
            self._sync_exclude(big_files)
        try:
            proc = self._git(["add", "--sparse", "."], check=False)
            if proc.returncode != 0:
                # 旧版 git(<2.25)不支持 --sparse:未启用 sparse-checkout 时
                # 普通 add 语义等价,回退即可。
                self._git(["add", "."], check=False)
        except SnapshotError:
            logger.warning("snapshot git add failed", exc_info=True)

    # ── 核心接口 ──────────────────────────────────────────────────────────

    def track(self) -> Optional[str]:
        """拍当前快照,返回 tree hash;disabled → None(规格 §5.5)。"""
        with self._lock():
            if not self.enabled():
                return None
            gd = self.gitdir()
            existed = gd.exists()
            if not existed:
                self._init_gitdir()
            self._add()
            proc = self._git(["write-tree"])
            tree = proc.stdout.strip()
            return tree or None

    def patch(self, hash: str) -> Patch:
        """自该 hash 后变更的文件清单(绝对路径)。规格 §5.6 patch()。"""
        with self._lock():
            empty = Patch(hash=hash, files=[])
            if not self.enabled() or not hash:
                return empty
            self._add()
            proc = self._git(
                ["diff", "--cached", "--no-ext-diff", "--name-only", "-z", hash, "--", "."],
                check=False,
            )
            rels = [p for p in proc.stdout.split("\0") if p]
            files: list[str] = []
            for rel in rels:
                abs_path = (self.worktree / rel).as_posix()
                if abs_path not in files:
                    files.append(abs_path)
            return Patch(hash=hash, files=files)

    def diff(self, hash: str) -> str:
        """自该 hash 后的完整 diff 文本。规格 §5.6 diff()。"""
        with self._lock():
            if not self.enabled() or not hash:
                return ""
            proc = self._git(
                ["-c", "core.quotepath=false", "diff", "--cached", "--no-ext-diff", hash, "--", "."],
                check=False,
            )
            return proc.stdout or ""

    def diff_full(self, from_hash: str, to_hash: str) -> list[FileDiff]:
        """每文件的 before/after、增减行、二进制标记。规格 §5.6 diffFull()。"""
        with self._lock():
            result: list[FileDiff] = []
            if not self.enabled() or not from_hash or not to_hash:
                return result
            status_proc = self._git(
                ["-c", "core.quotepath=false", "diff", "--no-ext-diff",
                 "--name-status", "--no-renames", from_hash, to_hash],
                check=False,
            )
            numstat_proc = self._git(
                ["-c", "core.quotepath=false", "diff", "--no-ext-diff",
                 "--numstat", "--no-renames", from_hash, to_hash],
                check=False,
            )
            numstat: dict[str, tuple[Optional[int], Optional[int]]] = {}
            for line in numstat_proc.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) < 2:
                    continue
                add_s, del_s = parts[0], parts[1]
                path = parts[2] if len(parts) > 2 else ""
                if not path:
                    continue
                if add_s == "-" or del_s == "-":
                    numstat[path] = (None, None)
                else:
                    try:
                        numstat[path] = (int(add_s), int(del_s))
                    except ValueError:
                        numstat[path] = (None, None)
            for line in status_proc.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    continue
                status = parts[0][0]
                rel = parts[1]
                added, removed = numstat.get(rel, (0, 0))
                binary = added is None or removed is None
                before = self._show(from_hash, rel)
                after = self._show(to_hash, rel)
                status_map = {"A": "added", "M": "modified", "D": "deleted"}
                result.append(FileDiff(
                    file=(self.worktree / rel).as_posix(),
                    status=status_map.get(status, "modified"),
                    binary=binary,
                    additions=0 if added is None else added,
                    deletions=0 if removed is None else removed,
                    before=before,
                    after=after,
                ))
            return result

    def _show(self, hash: str, rel: str) -> Optional[str]:
        """读取某快照内文件内容;文件中不存在(或二进制)返回 None。"""
        try:
            proc = self._git(
                ["-c", "core.quotepath=false", "show", f"{hash}:{rel}"],
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout
        except SnapshotError:
            return None
        return None

    # ── 回滚 ──────────────────────────────────────────────────────────────

    def restore(self, snapshot: str) -> None:
        """整仓回滚到某快照:read-tree + checkout-index,两条命令不碰用户 git。"""
        with self._lock():
            if not self.enabled() or not snapshot:
                return
            self._git(["read-tree", snapshot])
            self._git(["checkout-index", "-a", "-f"])

    def revert(self, patches: list[Patch]) -> None:
        """按文件精确回滚(合并去重),回到"该批步骤之前"的文件状态。§5.7。"""
        with self._lock():
            if not self.enabled() or not patches:
                return
            ops: list[dict] = []
            seen: set[str] = set()
            for patch in patches:
                if not patch.hash:
                    continue
                for file in patch.files:
                    if file in seen:
                        continue
                    seen.add(file)
                    try:
                        rel = Path(file).relative_to(self.worktree).as_posix()
                    except ValueError:
                        rel = Path(file).name
                    ops.append({"hash": patch.hash, "rel": rel})

            i = 0
            while i < len(ops):
                hash0 = ops[i]["hash"]
                batch: list[dict] = []
                batch_rels: list[str] = []
                for op in ops[i:]:
                    if op["hash"] != hash0 or len(batch) >= 100:
                        break
                    if any(_clash(op["rel"], r) for r in batch_rels):
                        break
                    batch.append(op)
                    batch_rels.append(op["rel"])
                try:
                    self._revert_batch(hash0, batch)
                except SnapshotError:
                    logger.warning("batched revert failed, falling back per-file", exc_info=True)
                    for op in batch:
                        try:
                            self._single(op["hash"], op["rel"])
                        except SnapshotError:
                            logger.warning("revert %s %s failed", op["hash"], op["rel"], exc_info=True)
                i += len(batch)

    def _revert_batch(self, hash: str, ops: list[dict]) -> None:
        """批量回滚:ls-tree 判存在,存在的批量 checkout,不存在的逐个删除。§5.7。"""
        rels = [op["rel"] for op in ops]
        proc = self._git(
            ["ls-tree", "--name-only", hash, "--", *rels], check=False, timeout=90
        )
        existing = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
        to_checkout = [op["rel"] for op in ops if op["rel"] in existing]
        if to_checkout:
            self._git(["checkout", hash, "--", *to_checkout], timeout=120)
        for op in ops:
            if op["rel"] not in existing:
                try:
                    (self.worktree / op["rel"]).unlink(missing_ok=True)
                except OSError:
                    pass

    def _single(self, hash: str, rel: str) -> None:
        """单文件回滚:checkout;失败时 ls-tree 判断"快照中存在但失败"还是"新建文件"。§5.7。"""
        proc = self._git(["checkout", hash, "--", rel], check=False)
        if proc.returncode == 0:
            return
        exists_proc = self._git(
            ["ls-tree", "--name-only", hash, "--", rel], check=False
        )
        in_tree = exists_proc.stdout.strip() != ""
        if not in_tree:
            try:
                (self.worktree / rel).unlink(missing_ok=True)
            except OSError:
                pass
        else:
            logger.warning("file %s exists in snapshot %s but checkout failed; keeping current", rel, hash)

    # ── 清理 ──────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """每小时任务:git gc --prune=7.days(规格 §5.8,窗口=最近 7 天)。"""
        with self._lock():
            if not self.enabled():
                return
            self._git_only(["gc", "--prune=7.days"], timeout=GC_TIMEOUT)


def _clash(a: str, b: str) -> bool:
    """两相对路径是否冲突(相同或互为祖先)。规格 §5.7 clash(a,b)。"""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


__all__ = [
    "DEFAULT_BIG_FILE_THRESHOLD",
    "FileDiff",
    "Patch",
    "Snapshot",
    "SnapshotError",
]