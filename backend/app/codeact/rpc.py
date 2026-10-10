"""宿主侧 CodeAct RPC 分发（设计 §7）。

沙盒内 `api.*` 调用经帧协议回到宿主，由本模块执行：
  - 文件类 RPC 复用 `app.tools.file_tools`，并镜像 `sub_tools.run_tool` 的权限桥
    （NeedsPermission → 发 permission_request → `mgr.await_decision` 等待 → 允许则临时放行重试）；
  - 路径先经 §4.4 校验（UNC / NTFS ADS / 重解析点 / 必须在 allowed_roots 内），**在 resolve 之前**；
  - `web_search` 受 `codeact_allow_network` 门控；`save_artifact` 只写 `data/codeact/<session_id>/`；
  - 结果统一 `bound_tool_output` 截断（复用主 Agent 语义，超限转储 `data/truncation/`）。
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple

from app.agent.stream_events import emit
from app.agent.sub_tools import (
    _coerce_args,
    _permission_denied_msg,
    _permission_request_payload,
)
from app.agent.web_search_agent import web_search_tool
from app.config import settings
from app.context.tool_output import bound_tool_output
from app.permission import NeedsPermission, get_manager as get_perm_mgr
from app.tools import file_tools as fs

logger = logging.getLogger(__name__)

_FILE_TOOLS = {
    "read_file": "tool_read_file",
    "write_file": "tool_write_file",
    "append_file": "tool_append_file",
    "list_dir": "tool_ls",
    "glob": "tool_glob",
    "grep": "tool_grep",
}
_WRITE_RPCS = {"write_file", "append_file"}
# [P0/B2] 硬编码 RPC 白名单，独立于 agent allowlist —— 绝不允许 tool_execute /
# tool_code_exec / 任何能再起 shell 或再起沙盒的工具名。沙盒内 api 只能调用这里列出的 fn。
_ALLOWED_RPC = frozenset(
    {"read_file", "write_file", "append_file", "list_dir", "glob", "grep", "web_search", "save_artifact"}
)
# [P0/B6] save_artifact 文件名非法字符（Windows 保留字符 + 路径分隔 + 通配符）
_ARTIFACT_NAME_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WIN_ILLEGAL_CHARS = re.compile(r'[<>"|\x00-\x1f]')


def _norm_case(p: str) -> str:
    s = os.path.normpath(p)
    return os.path.normcase(s) if os.name == "nt" else s


def _lexical_within(root: Path, cand: Path) -> bool:
    root_s = _norm_case(str(root))
    cand_s = _norm_case(str(cand))
    return cand_s == root_s or cand_s.startswith(root_s + os.sep)


def _reject_reason(user_path: str) -> Optional[str]:
    """§4.3：UNC / 非法字符 / 绝对路径穿越字符。"""
    if user_path.startswith("\\\\") or user_path.startswith("//"):
        return "UNC/网络路径"
    if _WIN_ILLEGAL_CHARS.search(user_path):
        return "包含非法字符"
    if os.name == "nt":
        norm = user_path.replace("/", "\\")
        rest = norm[2:] if len(norm) >= 2 and norm[1] == ":" and norm[0].isalpha() else norm
        if ":" in rest:
            return "NTFS 备用数据流(ADS)"
    return None


def _reparse_between(root: Path, cand: Path) -> Optional[str]:
    """从 cand 向上到 root 的每个组件做 lstat，发现重解析点即拒绝（§4.4 修正 6）。"""
    if os.name != "nt":
        return None
    root_s = _norm_case(str(root))
    cur = cand
    for _ in range(64):
        try:
            st = os.lstat(cur)
            if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
                return f"符号链接/重解析点: {cur}"
        except FileNotFoundError:
            pass
        except OSError:
            pass
        if _norm_case(str(cur)) == root_s:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def resolve_under_roots(user_path: str, roots: list[Path]) -> Tuple[Optional[Path], Optional[str]]:
    """把用户路径解析并校验在 allowed_roots 之内。返回 (resolved, error_text)。"""
    if not user_path:
        return None, "codeact: 路径为空"
    reason = _reject_reason(user_path)
    if reason:
        return None, f"codeact: 路径被拒绝（{reason}）: {user_path}"
    p = Path(user_path)
    if not p.is_absolute():
        p = roots[0] / user_path
    matched_root = next((r for r in roots if _lexical_within(r, p)), None)
    if matched_root is None:
        return None, (
            f"codeact: 路径超出允许范围: {p}\n"
            f"（仅允许会话工作目录 {roots[0]} 及 data/codeact、data/generated）"
        )
    reparse = _reparse_between(matched_root, p)
    if reparse:
        return None, f"codeact: 路径被拒绝（{reparse}）"
    try:
        resolved = p.resolve(strict=False)
    except OSError as e:
        return None, f"codeact: 路径解析失败: {e}"
    if not any(_lexical_within(r, resolved) for r in roots):
        return None, f"codeact: 路径解析后超出允许范围: {resolved}"
    return resolved, None


class RpcDispatcher:
    """一次 codeact 运行期间的 RPC 处理（绑定 ExecutionSession）。"""

    def __init__(self, session: Any):
        self.session = session

    @property
    def _roots(self) -> list[Path]:
        return self.session.read_roots

    async def handle(self, fn: str, args: dict) -> Tuple[bool, str, Optional[dict]]:
        # [P0/B2] 硬编码白名单 —— 不信任沙盒传的 fn，任何非名单内函数直接拒绝。
        if fn not in _ALLOWED_RPC:
            return False, "", {
                "code": "not_allowed",
                "msg": f"codeact: RPC '{fn}' 不在允许清单（仅 {_ALLOWED_RPC}）",
            }
        try:
            if fn in _FILE_TOOLS:
                roots = self.session.write_roots if fn in _WRITE_RPCS else self.session.read_roots
                result = await self._file_rpc(fn, args, roots)
            elif fn == "web_search":
                result = await self._web_search(args)
            elif fn == "save_artifact":
                result = await self._save_artifact(args)
            else:
                return False, "", {"code": "unknown_fn", "msg": f"未知 RPC: {fn}"}
        except Exception as e:  # noqa: BLE001
            logger.exception("codeact: rpc '%s' failed", fn)
            return False, "", {"code": "rpc_error", "msg": f"{type(e).__name__}: {e}"}
        return True, bound_tool_output(str(result), f"api.{fn}"), None

    async def _file_rpc(self, fn: str, args: dict, roots: list[Path]) -> str:
        fs_name = _FILE_TOOLS[fn]
        tool_fn = getattr(fs, fs_name)
        cargs = _coerce_args(tool_fn, dict(args))
        if "path" in inspect.signature(tool_fn).parameters:
            raw_path = str(cargs.get("path") or self.session.workspace_dir)
            resolved, err = resolve_under_roots(raw_path, roots)
            if err:
                return err
            cargs["path"] = str(resolved)
        result = await self._invoke(fs_name, tool_fn, cargs)
        if fn in _WRITE_RPCS and isinstance(result, str) and not result.startswith(
            ("Error", "Permission denied")
        ):
            try:
                self.session.record_file(str(cargs.get("path")))
            except Exception:  # noqa: BLE001
                pass
        return result

    async def _invoke(self, tool_name: str, tool_fn, cargs: dict) -> str:
        try:
            raw = await asyncio.to_thread(tool_fn, **cargs)
            return fs.unwrap(raw)
        except NeedsPermission as e:
            return await self._approve_and_retry(tool_name, tool_fn, cargs, e)
        except Exception as e:  # noqa: BLE001
            return f"Error executing {tool_name}: {e}"

    async def _approve_and_retry(self, tool_name, tool_fn, cargs, e: NeedsPermission) -> str:
        mgr = get_perm_mgr()
        req = mgr.create_request(e.path, e.operation, tool_name, cargs)
        emit(
            self.session.event_queue,
            _permission_request_payload(req, e.path, e.operation, tool_name, cargs),
        )
        if not self.session.event_queue:
            logger.warning("codeact: no event queue to approve %s", e.path)
            return _permission_denied_msg(e.operation, e.path)
        await self.session.on_approval_begin()
        try:
            decision = await mgr.await_decision(req.id)
        finally:
            await self.session.on_approval_end()
        if decision == "allowed":
            mgr.add_temp_approval(e.path)
            try:
                raw = await asyncio.to_thread(tool_fn, **cargs)
                return fs.unwrap(raw)
            except NeedsPermission:
                return _permission_denied_msg(e.operation, e.path)
        return _permission_denied_msg(e.operation, e.path)

    async def _web_search(self, args: dict) -> str:
        if not settings.codeact_allow_network:
            return "codeact: 网络访问已禁用（CODEACT_ALLOW_NETWORK=false）"
        query = str(args.get("query") or "").strip()
        if not query:
            return "codeact: web_search 需要参数 query"
        try:
            n = int(args.get("max_results") or 5)
        except (TypeError, ValueError):
            n = 5
        try:
            return await web_search_tool(query, max_results=max(1, min(n, 10)))
        except Exception as e:  # noqa: BLE001
            return f"codeact: web_search 失败: {e}"

    async def _save_artifact(self, args: dict) -> str:
        name = str(args.get("name") or "").strip()
        data = args.get("data", args.get("data_b64", ""))
        if not name:
            return "codeact: save_artifact 需要参数 name"
        # [P0/B6] 文件名 sanitize：拒绝非法字符 / 绝对路径 / ADS / 路径穿越
        if _ARTIFACT_NAME_ILLEGAL.search(name):
            return "codeact: save_artifact 文件名含非法字符（只允许纯文件名）"
        if name in (".", "..") or name.startswith((".", "..")):
            return "codeact: save_artifact 非法文件名"
        if name != os.path.basename(name.replace("\\", "/")):
            return "codeact: save_artifact 路径分隔符被拒绝（只允许纯文件名）"
        # double-extension 仍保留（如 .tar.gz）；但不允许空名
        try:
            raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
        except Exception as e:  # noqa: BLE001
            return f"codeact: 数据不是合法 base64: {e}"
        max_bytes = max(1, int(settings.codeact_max_artifact_size_mb) * 1024 * 1024)
        if len(raw) > max_bytes:
            return (
                f"codeact: 产物过大（{len(raw)} 字节 > 上限 "
                f"{settings.codeact_max_artifact_size_mb}MB），请缩小后再保存"
            )
        # 拒绝 Windows ADS（"file:stream"）
        if ":" in name:
            return "codeact: save_artifact 文件名含冒号（可能为 NTFS ADS）"
        dest = self.session.artifact_dir / name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        except OSError as e:
            return f"codeact: 写入产物失败: {e}"
        try:
            self.session.record_file(str(dest))
        except Exception:  # noqa: BLE001
            pass
        # 返回相对工作区的路径，方便模型后续 api.read_file 续读同一根下
        try:
            rel = dest.relative_to(self.session.workspace_dir)
            rel_s = str(rel).replace("\\", "/")
        except ValueError:
            rel_s = str(dest)
        return f"saved: {rel_s} ({len(raw)} bytes)"