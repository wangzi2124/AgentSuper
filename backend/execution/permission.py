import asyncio
import contextvars
import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Max number of temporary approvals before evicting oldest
_MAX_TEMP_APPROVALS = 1000
# TTL for temporary approvals in seconds (default: 5 minutes)
_TEMP_APPROVAL_TTL = 300

_manager: Optional["PermissionManager"] = None


def get_manager() -> "PermissionManager":
    """获取全局权限管理器单例实例。"""
    global _manager
    if _manager is None:
        _manager = PermissionManager()
    return _manager


def set_manager(m: "PermissionManager"):
    """设置全局权限管理器实例。"""
    global _manager
    _manager = m


# ── 会话级工作目录（opencode ctx.directory 对齐）────────────────────────────
# 用 contextvar 而非全局可变状态：多会话并发时互不干扰；asyncio.to_thread 会
# 复制当前 context，因此工具在 worker 线程内仍能看到本会话的目录。
_session_workspace_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_workspace", default=""
)


def set_session_workspace(directory: str) -> contextvars.Token:
    """把会话绑定的工作目录写入当前上下文，返回 token 供 reset。

    相对路径以此解析（见 file_tools._resolve），路径分类视其下为可写 workspace。
    仅设置该变量，不修改全局 PermissionManager 状态。
    """
    d = str(directory or "").strip()
    return _session_workspace_var.set(str(Path(d).resolve()) if d else "")


def reset_session_workspace(token: contextvars.Token) -> None:
    _session_workspace_var.reset(token)


def current_session_workspace() -> str:
    """当前上下文（本会话）绑定的工作目录；未设置返回空串�?"""
    return _session_workspace_var.get()


# Turn-scoped command approvals. Reset by the platform at the end of every turn.
_turn_command_approvals_var: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "turn_command_approvals", default=frozenset()
)


def add_turn_command_approval(base_cmd: str) -> None:
    key = str(base_cmd or "").strip().lower()
    if not key:
        return
    _turn_command_approvals_var.set(_turn_command_approvals_var.get() | {key})


def current_turn_command_approvals() -> frozenset[str]:
    return _turn_command_approvals_var.get()


def clear_turn_command_approvals() -> None:
    _turn_command_approvals_var.set(frozenset())


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_SPLIT = re.compile(r"\|\||&&|\s&\s|[|;\n]")
_TOKEN = re.compile(r"\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))")


def _first_token(segment: str) -> str:
    """取一段简单命令的首 token（跳过 `FOO=bar` 前缀），小写、去引号。"""
    pos = 0
    while pos < len(segment):
        match = _TOKEN.match(segment, pos)
        if not match:
            return ""
        token = next((g for g in match.groups() if g is not None), "")
        pos = match.end()
        if not token or _ENV_ASSIGN.match(token):
            continue
        return token.strip("'\"").lower()
    return ""


def command_keys(command: str) -> list[str]:
    """拆出命令中每个简单命令的首 token（命令白名单判定键）。

    只有**所有**段的 base 命令都被授权，整条命令才可免审；管道/串联里只要混入
    未授权命令就仍需询问用户。无法安全静态解析的写法（`$(...)`、反引号、
    重定向 `<`/`>`）返回空列表 → 强制人工审批，避免白名单变成任意执行的跳板。
    """
    text = str(command or "").strip()
    if not text or any(token in text for token in ("$(", "`", "<", ">")):
        return []
    keys: list[str] = []
    for segment in _SHELL_SPLIT.split(text):
        key = _first_token(segment.strip())
        if key and key not in keys:
            keys.append(key)
    return keys


def base_command(command: str) -> str:
    """提取命令白名单键：首个简单命令的第一个 token（跳过 `FOO=bar` 前缀）。

    仅用于展示/审批记录；判定请用 `command_keys`（多段命令逐段校验）。
    """
    keys = command_keys(command)
    if keys:
        return keys[0]
    text = str(command or "").strip()
    return _first_token(text) if text else ""


_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_DEVICE_FILES = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero"}
_REDIRECT_PREFIXES = ("2>>", "1>>", "2>", "1>", ">>", ">")


def _strip_command_token(token: str) -> str:
    """去掉重定向前缀 / 包裹引号 / 尾部标点，取出候选路径本体。"""
    t = token.strip()
    for prefix in _REDIRECT_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix) :]
            break
    t = t.strip().strip("'\"").rstrip(",;")
    # `--opt=/abs/path` / `NAME=C:\path`：取等号右侧
    if "=" in t and not _WIN_ABS_RE.match(t):
        left, _, right = t.partition("=")
        if right and left and not left.startswith("="):
            t = right.strip().strip("'\"")
    return t


def _looks_like_path(token: str) -> bool:
    """粗判一个 token 是否指向文件系统（避免把普通参数/URL 当路径）。"""
    if not token or _URL_RE.match(token):
        return False
    if token in _DEVICE_FILES:
        return False
    if _WIN_ABS_RE.match(token) or token.startswith("\\\\"):
        return True
    if token.startswith("~"):
        return True
    if token.startswith(("/", "./", "../", ".\\", "..\\")):
        # Windows 单字母开关（如 `dir /S`）不是路径
        if token.startswith("/") and len(token) <= 3 and token[1:].isalpha():
            return False
        return True
    # 含分隔符的相对路径（`src/main.py`、`dir\file.txt`）
    return "/" in token or "\\" in token


def command_path_tokens(command: str) -> list[str]:
    """从命令行里提取**可能访问文件系统**的路径 token。

    命令白名单只约束 base 命令（如 `cat`），无法阻止 `cat D:\\secret.txt`
    读取工作区外文件。这里把绝对路径、`~`、显式相对路径（`./`、`../`、含分隔符）
    抽出来，供审批层判断是否越界；普通参数/URL/Windows 开关会被忽略。
    """
    text = str(command or "")
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        raw = next((g for g in match.groups() if g is not None), "")
        token = _strip_command_token(raw)
        if _looks_like_path(token) and token not in out:
            out.append(token)
    return out


def is_git_path(path_str: str) -> bool:
    """路径任意层级含 `.git` → git 内部路径（读写一律拒绝）。"""
    try:
        p = Path(path_str).resolve()
    except (OSError, RuntimeError):
        p = Path(str(path_str))
    return any(part.lower() == ".git" for part in p.parts)


def is_sensitive_path(path_str: str) -> bool:
    """密钥 / 数据库 / 权限配置：即使位于工作目录内也不得静默访问。

    与 `PermissionManager._is_critical_read` 不同，这里按**绝对路径全名**判定，
    因此会话工作目录、额外工作区内的 `.env` / `*.db` / `permissions.json` 同样
    受保护（读取需审批、写入/执行直接拒绝）。
    """
    try:
        p = Path(path_str).resolve()
    except (OSError, RuntimeError):
        p = Path(str(path_str))
    if is_git_path(str(p)):
        return True
    name = p.name.lower()
    if name.startswith(".env") or name.endswith(".env"):
        return True
    if name.endswith((".db", ".sqlite", ".sqlite3")):
        return True
    return name in (
        "permissions.json",
        "command_permissions.json",
        "runtime_workspaces.json",
    )


class NeedsPermission(Exception):
    """当文件操作需要用户授权时抛出的异常。"""

    def __init__(self, path: str, operation: str, tool_name: str = "", tool_args: Optional[dict] = None):
        self.path = path
        self.operation = operation
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        super().__init__(f"Needs permission: {operation} {path}")


class PermissionRequest:
    """表示一个待审批的权限请求，包含操作路径、类型及异步等待机制。"""

    def __init__(self, path: str, operation: str, tool_name: str, tool_args: dict, session_id: str = ""):
        self.id = str(uuid.uuid4())
        self.path = path
        self.operation = operation
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.session_id = session_id
        self.status = "pending"
        self.created_at = datetime.now()
        self.responded_at: Optional[datetime] = None
        self.response: Optional[str] = None
        self.expires_at = self.created_at
        self._event = asyncio.Event()


class PermissionManager:
    """文件系统权限管理器，负责路径分类、权限检查、白名单管理和审批流程。"""

    def __init__(
        self,
        workspace: str = "",
        whitelist_path: str = "",
        extra_workspaces: Optional[list] = None,
        external_default: str = "ask",
        approval_timeout: int = 3600,
        allow_source_writes: bool = False,
        project_worktree: str = "",
    ):
        """初始化权限管理器，设置工作目录、白名单文件路径和额外工作区。

        allow_source_writes=True 时放行主工作区内受保护源码路径（app/plugins/
        skills/config/main.py 等）的写/执行；.git/.env/数据库等仍始终保护。
        project_worktree：git worktree（仓库根）。提供时其下路径也识别为
        workspace（对齐 opencode 的 worktree 语义），便于多项目/仓库根读写；
        源码保护仍以 self.workspace（backend/）相对路径判定，不受其影响。
        """
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
        self.allow_source_writes = bool(allow_source_writes)
        self.project_worktree = str(Path(project_worktree).resolve()) if project_worktree else ""
        whitelist_dir = Path(whitelist_path) if whitelist_path else self.workspace.parent / "data"
        self.data_dir = whitelist_dir if whitelist_dir.is_dir() else whitelist_dir.parent
        self.runtime_workspaces_path = self.data_dir / "runtime_workspaces.json"
        self.extra_workspaces: list[Path] = []
        if extra_workspaces:
            for item in extra_workspaces:
                item = str(item).strip()
                if item:
                    self.extra_workspaces.append(Path(item).resolve())
        self._load_runtime_workspaces()
        if external_default not in ("ask", "allow", "deny"):
            external_default = "ask"
        # 保留字段以兼容旧配置；决策已不再受它影响（外部路径一律人工审批）。
        self.external_default = external_default
        self.approval_timeout = max(1, int(approval_timeout))
        self._requests: dict[str, PermissionRequest] = {}
        # (resolved_path, operation) → request_id：同一路径同一操作并发触发时复用同一 pending 审批
        self._pending_by_key: dict[tuple[str, str], str] = {}
        self._temp_approvals: OrderedDict[str, float] = OrderedDict()
        # 命令临时授权（独立命名空间，不与路径临时授权混用）
        self._temp_command_approvals: OrderedDict[str, float] = OrderedDict()
        self._turn_command_approvals: dict[str, set[str]] = {}

    def _load_runtime_workspaces(self):
        """从 runtime_workspaces.json 加载前端「工作目录白名单」。

        这是可写白名单目录的唯一配置入口，持久化独立于 .env：重启后仍生效。
        """
        try:
            if self.runtime_workspaces_path.exists():
                data = json.loads(self.runtime_workspaces_path.read_text("utf-8"))
                existing = {self._workspace_key(w) for w in self.extra_workspaces}
                for item in data.get("extra_workspaces", []):
                    item = str(item).strip()
                    if not item:
                        continue
                    resolved = Path(item).resolve()
                    if self._workspace_key(resolved) not in existing:
                        self.extra_workspaces.append(resolved)
                        existing.add(self._workspace_key(resolved))
                if data.get("extra_workspaces"):
                    logger.info("Loaded %d runtime workspaces", len(data["extra_workspaces"]))
        except Exception as e:
            logger.warning("Failed to load runtime workspaces: %s", e)

    def _save_runtime_workspaces(self):
        """将当前白名单目录列表持久化到 JSON 文件。"""
        try:
            self.runtime_workspaces_path.parent.mkdir(parents=True, exist_ok=True)
            self.runtime_workspaces_path.write_text(
                json.dumps({"extra_workspaces": [str(w) for w in self.extra_workspaces]}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save runtime workspaces: %s", e)

    @staticmethod
    def _workspace_key(path: Path | str) -> str:
        """白名单去重键：Windows 大小写不敏感。"""
        import os

        return os.path.normcase(str(Path(path).resolve()))

    def check_command(self, base_cmd: str, session_id: str = "") -> str:
        """Command-level check; only current-turn approvals are honored.

        外部命令一律 ask（`EXTERNAL_PATH_DEFAULT` 不再参与决策）。
        """
        key = base_cmd.lower()
        if key in current_turn_command_approvals():
            return "allow"
        if session_id and key in self._turn_command_approvals.get(session_id, set()):
            return "allow"
        return "ask"

    def add_session_command_approval(self, session_id: str, base_cmd: str) -> None:
        key = str(base_cmd or "").strip().lower()
        if session_id and key:
            self._turn_command_approvals.setdefault(session_id, set()).add(key)

    def clear_session_command_approvals(self, session_id: str) -> None:
        self._turn_command_approvals.pop(session_id, None)

    def add_temp_command_approval(self, base_cmd: str):
        """命令级临时授权，TTL过期后自动失效。"""
        key = base_cmd.lower()
        self._temp_command_approvals[key] = time.time()
        # LRU 淘汰
        while len(self._temp_command_approvals) > _MAX_TEMP_APPROVALS:
            self._temp_command_approvals.popitem(last=False)

    def classify_path(self, path_str: str) -> str:
        """将路径分类为workspace/system/temp/external之一。"""
        p = Path(path_str).resolve()
        try:
            p.relative_to(self.workspace)
            return "workspace"
        except ValueError:
            pass
        # 会话绑定目录：当前上下文（本会话）的工作目录视为可写 workspace，
        # 与全局 extra_workspaces 同权（opencode：文件工具在 ctx.directory 下操作）。
        # ⚠️ 必须在 temp 判定之前：用户显式选择的工作目录即使恰好位于系统临时目录
        #    （Windows temp 很常见），也应识别为 workspace 而非被 temp 分支"劫持"，
        #    否则其下敏感文件（.env/数据库）会绕过 workspace 分支的保护检查。
        sw = _session_workspace_var.get()
        if sw:
            try:
                p.relative_to(Path(sw))
                return "workspace"
            except (ValueError, OSError):
                pass
        # 额外工作区 / 项目 worktree：均须在 temp 判定之前。
        # 同 session-dir 理由：若某额外工作区或仓库根恰好位于系统临时目录，
        # 被 temp→allow 劫持会让其下 .git/.env 绕过 Git 与敏感文件保护。
        for extra in self.extra_workspaces:
            try:
                p.relative_to(extra)
                return "workspace"
            except (ValueError, OSError):
                pass
        # 项目 worktree（git 仓库根）：其下路径视为 workspace（opencode worktree 语义）。
        # 与主工作区 backend/ 的关系：源码保护（_is_critical_write/_is_critical_read）
        # 仅以 self.workspace 相对路径判定，因此仓库根下但 backend/ 之外的部分
        # 不受 app/plugins/skills 保护名单约束。
        if self.project_worktree:
            try:
                p.relative_to(Path(self.project_worktree))
                return "workspace"
            except (ValueError, OSError):
                pass
        system_dirs = [
            "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
            "/etc", "/usr", "/bin", "/sbin", "/var",
        ]
        for sd in system_dirs:
            try:
                p.relative_to(Path(sd))
                return "system"
            except (ValueError, OSError):
                pass
        try:
            p.relative_to(Path(tempfile.gettempdir()).resolve())
            return "temp"
        except (ValueError, OSError):
            pass
        return "external"

    def _is_git_path(self, p: Path) -> bool:
        """路径任意层级含 .git（目录或 .git 文件）即为 git 内部路径，读写均拒绝。"""
        return is_git_path(str(p))

    def _is_critical_read(self, p: Path) -> bool:
        """判断是否为禁止读取的敏感路径（密钥/数据库/凭据文件）。"""
        try:
            rel = p.relative_to(self.workspace)
        except ValueError:
            return False
        parts = [part.lower() for part in rel.parts]
        name = parts[-1] if parts else ""
        if name.startswith(".env") or name.endswith(".env"):
            return True
        if name.endswith(".db") or name.endswith(".sqlite") or name.endswith(".sqlite3"):
            return True
        if "permissions.json" in name:
            return True
        return False

    def _is_critical_write(self, p: Path) -> bool:
        """判断是否为禁止写入/执行的敏感代码路径（源码、配置、插件、技能）。

        allow_source_writes=True 时放行这些源码路径（.git 仍始终拒绝）；
        .env/数据库/permissions.json 的写入由 _is_critical_read 兜底拒绝。
        """
        if self.allow_source_writes:
            try:
                rel = p.relative_to(self.workspace)
            except ValueError:
                return False
            return rel.parts and rel.parts[0].lower() == ".git"
        try:
            rel = p.relative_to(self.workspace)
        except ValueError:
            return False
        parts = [part.lower() for part in rel.parts]
        first = parts[0] if parts else ""
        if first in ("app", ".git"):
            return True
        if first in ("plugins", "skills", "config"):
            return True
        if len(parts) == 1 and parts[0] in (
            "main.py", "config.py", "requirements.txt", "pyproject.toml",
        ):
            return True
        return False

    def _under_any(self, p: Path, roots: list[str]) -> bool:
        r"""p 是否等于或位于 roots 任一路径之下（含多级子目录）。

        [外部目录授权] 以前临时授权只匹配「自身 + 直属父目录」一层，导致用户批准了
        `E:\some\project` 后，其**子目录**里的文件仍被判为未授权 —— 在
        tool_glob/tool_grep 里表现为结果被**静默丢弃**（既不能用也不提示）。
        改为整棵子树命中：批准一个目录 = 批准它下面所有内容。
        对文件路径无副作用（文件没有子路径，relative_to 只会匹配自身）。
        """
        for root in roots:
            try:
                p.relative_to(Path(root))
                return True
            except (ValueError, OSError):
                continue
        return False

    def check(self, path_str: str, operation: str) -> str:
        """检查指定路径的操作权限，返回allow/deny/ask。

        免审范围：当前会话工作目录（`set_session_workspace`）与白名单目录
        （`extra_workspaces`）。程序源码/仓库根不再隐式放行；其余路径一律 ask。
        任意层级含 `.git` 的路径读/写/执行一律 deny；.env/.db/permissions.json
        读取走 ask，写/执行直接 deny。
        """
        p = Path(path_str).resolve()
        if self._is_git_path(p):
            return "deny"
        cls = self.classify_path(path_str)
        if cls == "system":
            return "deny"
        if cls == "workspace":
            if self._is_critical_read(p):
                return "ask" if operation == "read" else "deny"
            if operation in ("write", "execute") and self._is_critical_write(p):
                return "ask"
            return "allow"
        now = time.time()
        # Clean up expired temp approvals
        expired = [k for k, t in self._temp_approvals.items() if now - t > _TEMP_APPROVAL_TTL]
        for k in expired:
            del self._temp_approvals[k]
        # [外部目录授权] 临时授权按**整棵子树**命中（批准目录 = 批准其下所有文件）
        if self._under_any(p, list(self._temp_approvals.keys())):
            for k in self._temp_approvals:
                if self._under_any(p, [k]):
                    self._temp_approvals.move_to_end(k)
                    break
            return "allow"
        return "ask"

    def add_workspace(self, path_str: str, create: bool = False) -> Path:
        """把目录加入白名单（免重启生效）。

        仅接受已存在的绝对目录；`create=True` 时缺失目录会被创建（前端二次确认后）。
        允许把仓库根加入白名单（源码写保护由审批层 `_is_critical_write` 兜底）。
        返回解析后的绝对路径；校验失败抛 ValueError。
        """
        raw = str(path_str or "").strip().strip('"').strip("'")
        if not raw:
            raise ValueError("path is required")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise ValueError("path must be an absolute path")
        p = p.resolve()
        if p.exists():
            if not p.is_dir():
                raise ValueError(f"not a directory: {p}")
        elif create:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ValueError(f"failed to create directory: {e}") from e
        else:
            raise FileNotFoundError(f"directory does not exist: {p}")
        if not os.access(p, os.R_OK | os.X_OK):
            raise ValueError(f"directory is not accessible: {p}")
        key = self._workspace_key(p)
        for wp in self.extra_workspaces:
            if self._workspace_key(wp) == key:
                return wp
        self.extra_workspaces.append(p)
        self._save_runtime_workspaces()
        return p

    def remove_workspace(self, path_str: str) -> bool:
        """从白名单移除目录，返回是否移除成功。"""
        p = Path(path_str).resolve()
        key = self._workspace_key(p)
        for i, wp in enumerate(self.extra_workspaces):
            if self._workspace_key(wp) == key:
                del self.extra_workspaces[i]
                self._save_runtime_workspaces()
                return True
        return False

    def list_workspaces(self) -> list[str]:
        """返回所有可写工作区（主工作区 + 白名单目录）的绝对路径。"""
        return [str(self.workspace), *(str(w) for w in self.extra_workspaces)]

    def list_extra_workspaces(self) -> list[str]:
        """仅返回用户经「工作目录白名单」配置的目录（不含程序主工作区）。

        平台审批的免审范围只用这些目录 + 会话工作目录 + 程序内部数据目录。
        """
        return [str(w) for w in self.extra_workspaces]

    def add_temp_approval(self, path_str: str):
        """临时授权指定路径，TTL 过期后自动失效。

        [外部目录授权] 只记录**被批准的那一个路径**，不再顺带记录父目录：
        旧实现附带 `p.parent` 是为了配合「只匹配一层」的判定；改用整棵子树命中后
        继续附带父目录会导致「批准一个文件 = 批准整个目录树」的越权。
        批准目录 → 其下全部内容生效；批准文件 → 仅该文件生效。
        """
        p = Path(path_str).resolve()
        now = time.time()
        self._temp_approvals[str(p)] = now
        # Evict oldest entries when over limit
        while len(self._temp_approvals) > _MAX_TEMP_APPROVALS:
            self._temp_approvals.popitem(last=False)

    def create_request(self, path: str, operation: str, tool_name: str = "", tool_args: Optional[dict] = None, session_id: str = "") -> PermissionRequest:
        """创建一条新的权限审批请求并返回。

        对同一 (path, operation) 复用仍处于 pending 的请求：同一轮内多个并发
        工具对同一路径触发 NeedsPermission 时只弹一次审批，所有等待者共享决定。
        """
        key = (str(Path(path).resolve()), operation)
        rid = self._pending_by_key.get(key)
        if rid:
            existing = self._requests.get(rid)
            if existing is not None and existing.status == "pending":
                return existing
        req = PermissionRequest(path, operation, tool_name, tool_args or {}, session_id)
        req.expires_at = datetime.now() + timedelta(seconds=self.approval_timeout)
        self._requests[req.id] = req
        self._pending_by_key[key] = req.id
        return req

    def _prune_pending_index(self, req: PermissionRequest) -> None:
        """请求进入终态后从 (path, operation) 去重索引移除（仅当索引仍指向自己）。"""
        key = (str(Path(req.path).resolve()), req.operation)
        if self._pending_by_key.get(key) == req.id:
            self._pending_by_key.pop(key, None)

    async def await_decision(self, request_id: str, timeout: Optional[int] = None) -> str:
        """异步等待用户对权限请求的审批结果，超时返回expired。"""
        if timeout is None:
            timeout = self.approval_timeout
        req = self._requests.get(request_id)
        if not req:
            return "expired"
        try:
            await asyncio.wait_for(req._event.wait(), timeout=timeout)
            return req.response or "denied"
        except asyncio.TimeoutError:
            req.status = "expired"
            self._prune_pending_index(req)
            return "expired"

    def respond(self, request_id: str, decision: str) -> bool:
        """响应权限请求：设置审批决定（仅本回合有效，不写入任何持久白名单）。"""
        req = self._requests.get(request_id)
        if not req or req.status != "pending":
            return False
        req.status = decision
        req.response = decision
        req.responded_at = datetime.now()
        req._event.set()
        # Approvals are turn-scoped by design: nothing is persisted here.
        self._prune_pending_index(req)
        self.cleanup_expired()
        return True

    def get_pending_requests(self) -> list[PermissionRequest]:
        """获取所有待审批的权限请求列表。"""
        return [r for r in self._requests.values() if r.status == "pending"]

    def get_request(self, request_id: str) -> Optional[PermissionRequest]:
        """根据请求ID获取权限请求详情。"""
        return self._requests.get(request_id)

    def cleanup_expired(self):
        """清理所有已处理（允许/拒绝/过期）的权限请求记录。"""
        expired = [rid for rid, r in self._requests.items() if r.status in ("allowed", "denied", "expired")]
        for rid in expired:
            req = self._requests.pop(rid, None)
            if req is not None:
                self._prune_pending_index(req)
