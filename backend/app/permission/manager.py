import asyncio
import contextvars
import json
import logging
import tempfile
import time
import uuid
from collections import OrderedDict
from datetime import datetime
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
    """当前上下文（本会话）绑定的工作目录；未设置返回空串。"""
    return _session_workspace_var.get()


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
        self.whitelist_path = whitelist_dir / "permissions.json" if whitelist_dir.is_dir() else whitelist_dir
        self.runtime_workspaces_path = self.whitelist_path.parent / "runtime_workspaces.json"
        self.extra_workspaces: list[Path] = []
        if extra_workspaces:
            for item in extra_workspaces:
                item = str(item).strip()
                if item:
                    self.extra_workspaces.append(Path(item).resolve())
        self._load_runtime_workspaces()
        if external_default not in ("ask", "allow", "deny"):
            external_default = "ask"
        self.external_default = external_default
        self.approval_timeout = max(1, int(approval_timeout))
        self._requests: dict[str, PermissionRequest] = {}
        # (resolved_path, operation) → request_id：同一路径同一操作并发触发时复用同一 pending 审批
        self._pending_by_key: dict[tuple[str, str], str] = {}
        self._temp_approvals: OrderedDict[str, float] = OrderedDict()
        # 命令级持久化白名单（独立于路径白名单，便于审计）
        self.command_whitelist_path = self.whitelist_path.parent / "command_permissions.json"
        self._command_whitelist: set[str] = set()
        # 命令临时授权（独立命名空间，不与路径临时授权混用）
        self._temp_command_approvals: OrderedDict[str, float] = OrderedDict()
        self._load_whitelist()
        self._load_command_whitelist()

    def _load_runtime_workspaces(self):
        """从 runtime_workspaces.json 加载前端运行时添加的工作目录。

        这是可写工作目录的唯一配置入口（替代已移除的 EXTRA_WORKSPACES 环境变量），
        持久化独立于 .env：重启后仍生效。
        """
        try:
            if self.runtime_workspaces_path.exists():
                data = json.loads(self.runtime_workspaces_path.read_text("utf-8"))
                existing = {str(w) for w in self.extra_workspaces}
                for item in data.get("extra_workspaces", []):
                    item = str(item).strip()
                    if item and str(Path(item).resolve()) not in existing:
                        self.extra_workspaces.append(Path(item).resolve())
                        existing.add(str(Path(item).resolve()))
                if data.get("extra_workspaces"):
                    logger.info("Loaded %d runtime workspaces", len(data["extra_workspaces"]))
        except Exception as e:
            logger.warning("Failed to load runtime workspaces: %s", e)

    def _save_runtime_workspaces(self):
        """将当前额外工作区列表持久化到 JSON 文件。"""
        try:
            self.runtime_workspaces_path.parent.mkdir(parents=True, exist_ok=True)
            self.runtime_workspaces_path.write_text(
                json.dumps({"extra_workspaces": [str(w) for w in self.extra_workspaces]}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save runtime workspaces: %s", e)

    def _load_whitelist(self):
        """从JSON文件加载已授权的路径白名单。"""
        self._whitelist: list[str] = []
        try:
            if self.whitelist_path.exists():
                data = json.loads(self.whitelist_path.read_text("utf-8"))
                self._whitelist = data.get("allowed_paths", [])
                logger.info("Loaded %d whitelist entries", len(self._whitelist))
        except Exception as e:
            logger.warning("Failed to load whitelist: %s", e)

    def _save_whitelist(self):
        """将当前白名单持久化保存到JSON文件。"""
        try:
            self.whitelist_path.parent.mkdir(parents=True, exist_ok=True)
            self.whitelist_path.write_text(
                json.dumps({"allowed_paths": self._whitelist}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save whitelist: %s", e)

    def _load_command_whitelist(self):
        """从JSON文件加载已授权的命令白名单。"""
        try:
            if self.command_whitelist_path.exists():
                data = json.loads(self.command_whitelist_path.read_text("utf-8"))
                self._command_whitelist = {c.lower() for c in data.get("allowed_commands", [])}
                logger.info("Loaded %d command whitelist entries", len(self._command_whitelist))
        except Exception as e:
            logger.warning("Failed to load command whitelist: %s", e)

    def _save_command_whitelist(self):
        """将当前命令白名单持久化保存到JSON文件。"""
        try:
            self.command_whitelist_path.parent.mkdir(parents=True, exist_ok=True)
            self.command_whitelist_path.write_text(
                json.dumps({"allowed_commands": sorted(self._command_whitelist)}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save command whitelist: %s", e)

    def check_command(self, base_cmd: str) -> str:
        """命令级权限检查，返回 "allow" / "ask" / "deny"。

        1. 命中持久化 command_whitelist -> "allow"；
        2. 命中临时命令授权（TTL 内）-> "allow"；
        3. 否则返回 self.external_default。
        """
        key = base_cmd.lower()
        # 持久化白名单
        if key in self._command_whitelist:
            return "allow"
        # 临时授权（含过期清理）
        now = time.time()
        expired = [k for k, t in self._temp_command_approvals.items() if now - t > _TEMP_APPROVAL_TTL]
        for k in expired:
            del self._temp_command_approvals[k]
        if key in self._temp_command_approvals:
            self._temp_command_approvals.move_to_end(key)
            return "allow"
        return self.external_default

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
        return any(part.lower() == ".git" for part in p.parts)

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

    def check(self, path_str: str, operation: str) -> str:
        """检查指定路径的操作权限，返回allow/deny/ask。

        敏感路径（.git/.env/.db）的读操作统一返回 ask → 前端弹审批对话框，
        用户允许后临时授权；写/执行仍 ask → 前端弹审批对话框 用户允许后临时授权。
        """
        cls = self.classify_path(path_str)
        if cls == "system":
            return "deny"
        if cls == "temp":
            return "allow"
        p = Path(path_str).resolve()
        if cls == "workspace":
            if self._is_git_path(p):
                # .git 读取弹窗审批
                return "ask"
            if self._is_critical_read(p):
                # .env/.db 读取弹窗审批
                return "ask"
            if operation in ("write", "execute") and self._is_critical_write(p):
                # write/execute 读取弹窗审批
                return "ask"
            return "allow"
        now = time.time()
        # Clean up expired temp approvals
        expired = [k for k, t in self._temp_approvals.items() if now - t > _TEMP_APPROVAL_TTL]
        for k in expired:
            del self._temp_approvals[k]
        if str(p) in self._temp_approvals:
            self._temp_approvals.move_to_end(str(p))
            return "allow"
        if str(p.parent) in self._temp_approvals:
            self._temp_approvals.move_to_end(str(p.parent))
            return "allow"
        for allowed in self._whitelist:
            try:
                Path(p).relative_to(Path(allowed).resolve())
                return "allow"
            except ValueError:
                pass
        return self.external_default

    def add_workspace(self, path_str: str) -> Path:
        """运行时新增可写工作区（免重启生效），目录不存在则自动创建。

        返回解析后的绝对路径。
        """
        p = Path(path_str).resolve()
        if p in self.extra_workspaces:
            return p
        self.extra_workspaces.append(p)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Failed to create workspace dir %s: %s", p, e)
        self._save_runtime_workspaces()
        return p

    def remove_workspace(self, path_str: str) -> bool:
        """运行时移除工作区，返回是否移除成功。"""
        p = Path(path_str).resolve()
        for i, wp in enumerate(self.extra_workspaces):
            if wp == p:
                del self.extra_workspaces[i]
                self._save_runtime_workspaces()
                return True
        return False

    def list_workspaces(self) -> list[str]:
        """返回所有可写工作区（主工作区 + 额外工作区）的绝对路径。"""
        return [str(self.workspace), *(str(w) for w in self.extra_workspaces)]

    def add_temp_approval(self, path_str: str):
        """临时授权指定路径（及其父目录），TTL过期后自动失效。"""
        p = Path(path_str).resolve()
        now = time.time()
        self._temp_approvals[str(p)] = now
        self._temp_approvals[str(p.parent)] = now
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

    def respond(self, request_id: str, decision: str, remember: bool = False) -> bool:
        """响应权限请求：设置审批决定，可选记住该路径到白名单。"""
        req = self._requests.get(request_id)
        if not req or req.status != "pending":
            return False
        req.status = decision
        req.response = decision
        req.responded_at = datetime.now()
        req._event.set()
        if remember and decision == "allowed":
            if req.operation == "command":
                # 命令维度：记住 base 命令（不记参数）
                key = req.path.lower().strip()
                if key and key not in self._command_whitelist:
                    self._command_whitelist.add(key)
                    self._save_command_whitelist()
            else:
                p = str(Path(req.path).resolve())
                if p not in self._whitelist:
                    self._whitelist.append(p)
                    self._save_whitelist()
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
