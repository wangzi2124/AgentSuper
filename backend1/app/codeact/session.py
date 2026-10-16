"""CodeAct 受控执行会话（设计 §3/§5/§6），宿主侧调度 一次执行的一个沙盒子进程。

一次 run = 一个新 (回环 socket server + 子进程 + Job Object)。子进程以
`[sys.executable, -I, -X, utf8, bootstrap.py, <config_path>]` 拉起，通过长度前缀
JSON 帧（result.py）通信：
  - hello（token 握手）→ rpc / rpc_resp 往返（经 RpcDispatcher 复用 file_tools +
    审批桥）→ result（CodeActResult）。

超时模型（v2.1 修正 9）：`base = 启动 + codeact_timeout`，审批等待期间
`grace_total` 累加暂停时长（方被授权弹窗无限拖时长任务的假死不被误杀）；
看门狗持续轮询 `now >= base + grace_total` 才判超时。超时/崩溃/提前退出
一律收拾干净：Job terminate（失败回退 taskkill /T /F）、关闭 socket、删配置文件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from app.codeact.ast_guard import analyze_code
from app.codeact.job_object import JobObject
from app.codeact.kill_tree import kill_tree
from app.codeact.result import CodeActResult, FrameCodecError, decode_frame, encode_frame
from app.codeact.result import _MAX_FRAME_BYTES
from app.codeact.rpc import RpcDispatcher
from app.codeact.sdk_template import build_api_source
from app.config import settings
from app.storage.paths import global_paths

logger = logging.getLogger(__name__)

# [P0/B5] 子进程 stderr 脱敏：Windows 用户目录路径 + 疑似密钥
_USERDIR_RE = re.compile(r"(?i)([a-z]:\\\\users\\\\)[^\\\\]+")
_SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]+|"
    r"api[_-]?key\s*[=:]\s*\S+)"
)

# 子进程环境变量白名单（§4.0 env-wl）——其余（含（疑似敏感）变量）一律不带入
_ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "LANG",
)

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200

# [§6] 全局并发限流：CODEACT_MAX_CONCURRENT 个沙盒同时运行，超出则排队等待。
# 懒创建（首次 run 时在事件循环内构造），避免 import 期无 loop。
_codeact_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _codeact_semaphore
    if _codeact_semaphore is None:
        _codeact_semaphore = asyncio.Semaphore(max(1, int(settings.codeact_max_concurrent)))
    return _codeact_semaphore


def _sanitize_text(text: str) -> str:
    """[P0/B5] 沙盒崩溃 stderr 回灌前脱敏（用户目录路径 + 疑似密钥）。"""
    text = _USERDIR_RE.sub(r"\1<user>", text)
    text = _SECRET_RE.sub("<redacted>", text)
    return text


class ExecutionSession:
    """单次 CodeAct 执行：spawn → 握手 → RPC → result，带超时/审批暂停/进程树清理。"""

    def __init__(
        self,
        code: str,
        workspace_dir: Path,
        *,
        conversation_id: str = "",
        session_id: str = "",
        event_queue: Any = None,
        run_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.code = code
        self.workspace_dir = Path(workspace_dir).resolve()
        self.conversation_id = conversation_id
        self.session_id = session_id or conversation_id or "unnamed"
        self.event_queue = event_queue
        self.run_id = run_id or secrets.token_hex(8)
        # [P0/B1] timeout 只能调小，不能调大：effective = min(requested|默认, sys 上限)
        cap = max(1.0, float(settings.codeact_timeout))
        self.timeout = cap if timeout is None else min(max(1.0, float(timeout)), cap)
        self.rpc_timeout = max(1.0, float(settings.codeact_rpc_timeout))

        data_dir = Path(global_paths()["data"])
        self.run_dir = data_dir / "codeact" / "run" / self.run_id
        self.artifact_dir = data_dir / "codeact" / self.session_id
        truncation = data_dir / "truncation"
        # [P0/B4] 读根含 data/truncation（bound_tool_output 截断转储可被 api.read_file 续读）；
        #         写根不含它——只读。
        self.read_roots: list[Path] = [
            self.workspace_dir,
            data_dir / "codeact",
            data_dir / "generated",
            truncation,
        ]
        self.write_roots: list[Path] = [
            self.workspace_dir,
            data_dir / "codeact",
            data_dir / "generated",
        ]
        self._allowed_roots_cache: Optional[list[Path]] = None

        self.files: list[str] = []
        self.dispatcher = RpcDispatcher(self)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if str(self.workspace_dir) != str(self.artifact_dir):
            self.artifact_dir.mkdir(parents=True, exist_ok=True)

        self._token = secrets.token_hex(16)
        self._pid: Optional[int] = None
        self._job: Optional[JobObject] = None
        self._proc: Optional[subprocess.Popen] = None
        self._cfg_path: Optional[Path] = None
        self._start = time.monotonic()
        self._grace_total = 0.0
        self._pause_depth = 0
        self._pause_start: Optional[float] = None
        self._result: Optional[CodeActResult] = None
        self._done = asyncio.Event()
        self._ready = asyncio.Event()
        self._client_writer: Any = None
        self._server: Any = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._clean = False

    # ── 审批/宿主等待时钟钩子（rpc.py 与 _on_client 调用）──

    async def on_approval_begin(self) -> None:
        # [P1/C1] 宿主侧任何等待（审批弹窗 / web_search 等 RPC 也计入）都暂停执行超时，
        #         嵌套 begin 用深度计数合并
        self._pause_depth += 1
        if self._pause_depth == 1:
            self._pause_start = time.monotonic()

    async def on_approval_end(self) -> None:
        self._pause_depth = max(0, self._pause_depth - 1)
        if self._pause_depth == 0 and self._pause_start is not None:
            self._grace_total += time.monotonic() - self._pause_start
            self._pause_start = None

    def record_file(self, path: str) -> None:
        if path and path not in self.files:
            self.files.append(path)

    # ── 主流程 ──

    async def run(self) -> CodeActResult:
        # [§4.1] 第一层 AST 静态守卫：宿主在 spawn 子进程**之前**调用；
        # 拒绝即回结构化错误（`ast_rejected`），不进沙盒（LLM 读错误后改写）。
        reject = analyze_code(
            self.code,
            max_code_chars=int(settings.codeact_max_code_chars),
            max_ast_nodes=int(settings.codeact_max_ast_nodes),
        )
        if reject:
            return self._error_result("ast_rejected", reject)
        # [§6] 全局并发限流：超出 CODEACT_MAX_CONCURRENT 的请求在这里排队，
        # 排队时长不计入执行超时（执行超时从 _run_locked 起算）。
        async with _get_semaphore():
            return await self._run_locked()

    async def _run_locked(self) -> CodeActResult:
        duration = time.perf_counter()
        try:
            self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
            port = self._server.sockets[0].getsockname()[1]
            self._write_config(port)
            self._spawn()
            self._watchdog_task = asyncio.create_task(self._watchdog())
            try:
                await self._done.wait()
            except asyncio.CancelledError:
                self._finalize(self._error_result("cancelled", "codeact: 执行被取消"))
                raise
            result = self._result or self._error_result("no_result", "codeact: 未收到结果")
            result.duration_ms = (time.perf_counter() - duration) * 1000
            result.files = list(self.files)
            return result
        except FrameCodecError as e:
            return self._error_result("frame_error", f"codeact: 帧协议错误: {e}")
        except OSError as e:
            return self._error_result("os_error", f"codeact: 启动沙盒失败: {e}")
        finally:
            self._finalize(None)

    def _write_config(self, port: int) -> None:
        cfg = {
            "port": port,
            "token": self._token,
            "code": self.code,
            "sdk_source": build_api_source(),
            "max_output_chars": int(settings.codeact_max_output_chars),
        }
        cfg_path = self.run_dir / f"run.json"
        cfg_path.write_text(
            json.dumps(cfg, ensure_ascii=False),
            encoding="utf-8",
        )
        self._cfg_path = cfg_path

    def _spawn(self) -> None:
        self._job = JobObject.create(
            int(settings.codeact_max_memory_mb),
            int(settings.codeact_max_processes),
        )
        bootstrap = Path(os.path.dirname(os.path.abspath(__file__))) / "bootstrap.py"
        flags = 0
        if os.name == "nt":
            flags |= _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        env = {k: os.environ[k] for k in _ENV_WHITELIST if k in os.environ}
        # [P1/C7] -S：不加载 site（白名单全是标准库，_safe_import 无需 site-packages）。
        # [P2/D4] -X pycache_prefix：把白名单模块编译的 __pycache__ 收进 run_dir，随 run 目录一起清
        pycache_dir = self.run_dir / "pycache"
        pycache_dir.mkdir(parents=True, exist_ok=True)
        out_log = open(self.run_dir / "stdout.log", "wb")
        err_log = open(self.run_dir / "stderr.log", "wb")
        self._proc = subprocess.Popen(
            [
                sys.executable, "-I", "-S", "-X", "utf8",
                f"-Xpycache_prefix={pycache_dir}",
                str(bootstrap), str(self._cfg_path),
            ],
            cwd=str(self.workspace_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_log,
            stderr=err_log,
            creationflags=flags,
        )
        self._pid = self._proc.pid
        out_log.close()
        err_log.close()
        logger.info("codeact: spawned pid=%s run=%s", self._pid, self.run_id)
        # [v2.1 修正 9 / v2.1.1-P0/A] Popen 只保留进程句柄（主线程句柄创建后即关闭），
        # ResumeThread(进程句柄) = ERROR_INVALID_HANDLE，故放弃 CREATE_SUSPENDED；
        # 改为「先建 Job → 正常创建 → 立即 assign」。**assign 失败必须马上 kill**：
        # 子进程已在跑但不受 Job 约束，窗口从「极小」变「无限」，不能只记 warning。
        # 窗口内的执行面由 bootstrap 的 ready 帧协议封死（连接→token→白名单→hook→ready 后才 exec，
        # 宿主在 ready 前不派发任何 RPC）。Phase 2 若要真无窗口，需 pywin32/ctypes 走
        # CreateProcessW 拿 hThread 实现 CREATE_SUSPENDED → assign → ResumeThread。
        if self._job is None:
            return
        if not self._job.assign(self._proc._handle):
            logger.error(
                "codeact: AssignProcessToJobObject failed; killing pid=%s immediately",
                self._pid,
            )
            self._job.close()
            self._job = None
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                kill_tree(self._pid)
            self._done.set()
            self._result = self._error_result(
                "spawn_failed", "codeact: 无法将沙盒纳入资源限制（Job Object 分配失败），已终止"
            )

    # ── 客户端回调（frame 循环）──

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._client_writer = writer
        try:
            hello = await self._read_frame(reader)
            if hello.get("ch") != "hello" or not secrets.compare_digest(
                str(hello.get("token") or ""), self._token
            ):
                await self._write({"ch": "hello", "ok": False})
                return
            await self._write({"ch": "hello", "ok": True})
            while not self._done.is_set():
                frame = await self._read_frame(reader)
                ch = frame.get("ch")
                if ch == "result":
                    logger.info("codeact: result ok=%s run=%s", frame.get("ok"), self.run_id)
                    self._result = CodeActResult.from_frame(frame)
                    self._done.set()
                    break
                elif ch == "ready":
                    # [P0/A] 就绪门：只在收到 ready 后放行 RPC（hook 就位 + api 构建完成）
                    self._ready.set()
                elif ch == "rpc":
                    # [P1/C1] 任何宿主侧等待（审批/web_search/读文件等）都暂停执行超时，
                    #         嵌套 on_approval_begin/end 深度合并重叠区间
                    rid = frame.get("id")
                    await self.on_approval_begin()
                    try:
                        await asyncio.wait_for(self._ready.wait(), timeout=self.rpc_timeout)
                        ok, result, error = await asyncio.wait_for(
                            self.dispatcher.handle(str(frame.get("fn") or ""), frame.get("args") or {}),
                            timeout=self.rpc_timeout,
                        )
                    except asyncio.TimeoutError:
                        ok, result, error = False, "", {
                            "code": "rpc_timeout",
                            "msg": f"RPC '{frame.get('fn')}' 超时（>{self.rpc_timeout:.0f}s）",
                        }
                    except Exception as e:  # noqa: BLE001
                        ok, result, error = False, "", {
                            "code": "rpc_error",
                            "msg": f"{type(e).__name__}: {e}",
                        }
                    finally:
                        await self.on_approval_end()
                    await self._write(
                        {"ch": "rpc_resp", "id": rid, "ok": ok, "result": result, "error": error}
                    )
                else:
                    logger.warning("codeact: 未知帧 ch=%s", ch)
                    break
        except (ConnectionError, EOFError, asyncio.IncompleteReadError, FrameCodecError):
            if not self._done.is_set():
                self._result = self._error_crash_result()
                self._done.set()
        except Exception as e:  # noqa: BLE001
            logger.exception("codeact: client loop error")
            if not self._done.is_set():
                self._result = self._error_result("protocol_error", f"codeact: 协议错误: {e}")
                self._done.set()
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _read_frame(self, reader: asyncio.StreamReader) -> dict:
        header = await reader.readexactly(4)
        (size,) = struct.unpack(">I", header)
        # [P2/D2] 与 encode 侧对称：拒绝超限 declared size，避免恶意子进程撑爆宿主内存
        if size > _MAX_FRAME_BYTES:
            raise FrameCodecError(f"frame too large: {size} bytes")
        body = await reader.readexactly(size)
        return decode_frame(header + body)

    async def _write(self, payload: dict) -> None:
        if self._client_writer is None:
            raise ConnectionError("codeact: 客户端连接不存在")
        self._client_writer.write(encode_frame(payload))
        await self._client_writer.drain()

    # ── 看门狗 ──

    async def _watchdog(self) -> None:
        try:
            while not self._done.is_set():
                elapsed = time.monotonic() - self._start
                if elapsed >= self.timeout + self._grace_total:
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        if not self._done.is_set():
            logger.warning(
                "codeact: 执行超时（run=%s pid=%s, %.1fs > %.1fs）",
                self.run_id, self._pid, time.monotonic() - self._start,
                self.timeout + self._grace_total,
            )
            self._result = self._error_result(
                "timeout",
                f"codeact: 执行超时（>{self.timeout:.0f}s，审批等待不计入）；请缩小运算/循环规模",
            )
            self._done.set()

    # ── 收尾 ──

    def _finalize(self, result: Optional[CodeActResult]) -> None:
        if self._clean:
            return
        self._clean = True
        self._done.set()
        if result is not None and self._result is None:
            self._result = result
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        self._kill()
        if self._server is not None:
            self._server.close()
        try:
            if self._cfg_path is not None:
                self._cfg_path.unlink(missing_ok=True)
        except OSError:  # noqa: BLE001
            pass

    def _kill(self) -> None:
        if self._job is not None:
            if not self._job.terminate() and self._pid:
                kill_tree(self._pid)
            self._job.close()
            self._job = None
        elif self._pid:
            kill_tree(self._pid)
            self._pid = None

    def _error_result(self, code: str, msg: str) -> CodeActResult:
        return CodeActResult(ok=False, error={"code": code, "msg": msg})

    def _error_crash_result(self) -> CodeActResult:
        """连接断开（子进程崩溃）时，读 stderr.log 尾部、脱敏、并映射内存超限退出码。"""
        # [P1/C6] Windows 内存承诺限制杀进程的退出码：STATUS_COMMITMENT_LIMIT = 0xC000012D
        code, msg = "sandbox_exited", "codeact: 沙盒连接提前断开（子进程崩溃或被杀）"
        if self._proc is not None:
            try:
                rc = self._proc.poll()
            except Exception:  # noqa: BLE001
                rc = None
            if rc == 0xC000012D or (isinstance(rc, int) and rc < 0 and rc & 0xFFFFFFFF == 0xC000012D):
                code, msg = "memory_limit", "codeact: 沙盒内存超限被终止（Job Object 内存限制生效）"
        tail = ""
        try:
            err_log = self.run_dir / "stderr.log"
            if err_log.exists() and err_log.stat().st_size > 0:
                with open(err_log, "rb") as f:
                    f.seek(max(0, err_log.stat().st_size - 4000))
                    tail = f.read().decode("utf-8", "replace")
        except OSError:  # noqa: BLE001
            pass
        if tail:
            tail = _sanitize_text(tail.strip())[-2000:]
            msg = f"{msg}\n--- child stderr (tail, sanitized) ---\n{tail}"
        return self._error_result(code, msg)