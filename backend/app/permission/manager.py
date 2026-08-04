import asyncio
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
        approval_timeout: int = 60,
    ):
        """初始化权限管理器，设置工作目录、白名单文件路径和额外工作区。"""
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
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
        self._temp_approvals: OrderedDict[str, float] = OrderedDict()
        self._load_whitelist()

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

    def classify_path(self, path_str: str) -> str:
        """将路径分类为workspace/system/temp/external之一。"""
        p = Path(path_str).resolve()
        try:
            p.relative_to(self.workspace)
            return "workspace"
        except ValueError:
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
        for extra in self.extra_workspaces:
            try:
                p.relative_to(extra)
                return "workspace"
            except (ValueError, OSError):
                pass
        return "external"

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
        """判断是否为禁止写入/执行的敏感代码路径（源码、配置、插件、技能）。"""
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
        if len(parts) == 1 and parts[0] in ("main.py", "requirements.txt", "pyproject.toml"):
            return True
        return False

    def check(self, path_str: str, operation: str) -> str:
        """检查指定路径的操作权限，返回allow/deny/ask。"""
        cls = self.classify_path(path_str)
        if cls == "system":
            return "deny"
        if cls == "temp":
            return "allow"
        p = Path(path_str).resolve()
        if cls == "workspace":
            if self._is_critical_read(p):
                return "deny"
            if operation in ("write", "execute") and self._is_critical_write(p):
                return "deny"
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
        """创建一条新的权限审批请求并返回。"""
        req = PermissionRequest(path, operation, tool_name, tool_args or {}, session_id)
        self._requests[req.id] = req
        return req

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
            p = str(Path(req.path).resolve())
            if p not in self._whitelist:
                self._whitelist.append(p)
                self._save_whitelist()
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
            self._requests.pop(rid, None)
