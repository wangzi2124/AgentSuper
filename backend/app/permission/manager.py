import asyncio
import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_manager: Optional["PermissionManager"] = None


def get_manager() -> "PermissionManager":
    global _manager
    if _manager is None:
        _manager = PermissionManager()
    return _manager


def set_manager(m: "PermissionManager"):
    global _manager
    _manager = m


class NeedsPermission(Exception):
    def __init__(self, path: str, operation: str, tool_name: str = "", tool_args: Optional[dict] = None):
        self.path = path
        self.operation = operation
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        super().__init__(f"Needs permission: {operation} {path}")


class PermissionRequest:
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
    def __init__(self, workspace: str = "", whitelist_path: str = ""):
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
        whitelist_dir = Path(whitelist_path) if whitelist_path else self.workspace.parent / "data"
        self.whitelist_path = whitelist_dir / "permissions.json" if whitelist_dir.is_dir() else whitelist_dir
        self._requests: dict[str, PermissionRequest] = {}
        self._temp_approvals: set[str] = set()
        self._load_whitelist()

    def _load_whitelist(self):
        self._whitelist: list[str] = []
        try:
            if self.whitelist_path.exists():
                data = json.loads(self.whitelist_path.read_text("utf-8"))
                self._whitelist = data.get("allowed_paths", [])
                logger.info("Loaded %d whitelist entries", len(self._whitelist))
        except Exception as e:
            logger.warning("Failed to load whitelist: %s", e)

    def _save_whitelist(self):
        try:
            self.whitelist_path.parent.mkdir(parents=True, exist_ok=True)
            self.whitelist_path.write_text(
                json.dumps({"allowed_paths": self._whitelist}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save whitelist: %s", e)

    def classify_path(self, path_str: str) -> str:
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
        return "external"

    def check(self, path_str: str, operation: str) -> str:
        cls = self.classify_path(path_str)
        if cls == "workspace":
            return "allow"
        if cls == "system":
            return "deny"
        if cls == "temp":
            return "allow"
        p = Path(path_str).resolve()
        if str(p) in self._temp_approvals:
            return "allow"
        if str(p.parent) in self._temp_approvals:
            return "allow"
        for allowed in self._whitelist:
            try:
                Path(p).relative_to(Path(allowed).resolve())
                return "allow"
            except ValueError:
                pass
        return "ask"

    def add_temp_approval(self, path_str: str):
        p = Path(path_str).resolve()
        self._temp_approvals.add(str(p))
        self._temp_approvals.add(str(p.parent))

    def create_request(self, path: str, operation: str, tool_name: str = "", tool_args: Optional[dict] = None, session_id: str = "") -> PermissionRequest:
        req = PermissionRequest(path, operation, tool_name, tool_args or {}, session_id)
        self._requests[req.id] = req
        return req

    async def await_decision(self, request_id: str, timeout: int = 120) -> str:
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
        return True

    def get_pending_requests(self) -> list[PermissionRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def get_request(self, request_id: str) -> Optional[PermissionRequest]:
        return self._requests.get(request_id)

    def cleanup_expired(self):
        expired = [rid for rid, r in self._requests.items() if r.status in ("allowed", "denied", "expired")]
        for rid in expired:
            self._requests.pop(rid, None)
