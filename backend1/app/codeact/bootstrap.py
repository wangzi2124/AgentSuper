"""CodeAct 沙盒运行时（设计 §4，bootstrap）。

以 stdin 之外的独立子进程运行，由宿主以
`[sys.executable, "-I", "-X", "utf8", "<bootstrap.py>", <config_path>]` 拉起。
仅使用标准库；通过 `importlib.util` 按路径挂载同目录 `result.py`（不 import app 包），
避免把宿主应用代码引进沙盒。

职责链（§4.2）：
  1. 读配置 → 2. 连接宿主回环 socket + hello 握手 → 3. 预加载白名单模块（hook 安装前）
     → 4. 清理 sys.modules 危险模块 → 5. 构造 SAFE_BUILTINS（含安全 __import__）
     → 6. 安装 audit hook → 7. 从 SDK_SOURCE 构建 api 模块 → 8. exec（stdout/stderr
     重定向 + last_expr AST 改写）→ 9. 发 result 帧。

`if __name__ == "__main__"` 守卫保证宿主可安全 import 本模块（§4.2）。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import socket
import struct
import sys
import traceback
import types

# 与 app/codeact/ast_guard.py 的 IMPORT_WHITELIST 保持一致（本文件 stdlib-only，避免引 app 包）
_IMPORT_WHITELIST = frozenset({
    "math", "json", "re", "statistics", "datetime", "itertools", "functools",
    "collections", "decimal", "fractions", "random", "textwrap", "csv",
    "base64", "hashlib", "uuid", "unicodedata", "string",
})

# audit hook 额外可见（import 语句层面也由安全 __import__ 兜底）
_IMPORT_ALLOWED_AUDIT = _IMPORT_WHITELIST | {"api"}

# 用户 exec 期间禁止的 audit 事件。RPC 回环自身只用到 send/recv 等白名单内事件。
_BLOCKED_AUDIT = {
    "open", "io.open_code", "os.open", "os.listdir", "os.scandir", "os.system",
    "os.fork", "os.exec", "os.spawn", "os.popen", "subprocess.Popen",
    "subprocess.call", "subprocess.run", "ctypes", "platform.system",
    "socket.socket", "socket.getaddrinfo", "socket.getnameinfo",
    "socket.connect", "socket.bind", "select.select", "sys.setprofile",
    "sys.settrace", "capi.create_voidptr",
}
_ALLOWED_SOCKET_AUDIT = {
    "socket.send", "socket.recv", "socket.__new__",
    "socket.getpeername", "socket.getsockname", "socket.fileno",
}

# 预加载完成后从 sys.modules 清理的危险模块（sys/os 保留：白名单模块持有引用，
# 且 import/audit 双闸已封死用户侧获取途径）
_EVICT_MODULES = {
    "subprocess", "socket", "ctypes", "importlib", "pathlib", "urllib", "http",
    "requests", "shutil", "signal", "pty", "tempfile", "glob", "pickle",
    "marshal", "shelve", "dbm", "code", "codeop", "zipfile", "tarfile",
    "platform", "select", "multiprocessing", "threading", "queue", "telnetlib",
    "ftplib", "smtplib", "imaplib", "pty", "sysconfig",
}

# IPC 第一级截断（防构造超 16MB 帧打爆宿主）；LLM 侧由 CodeActResult.to_text 再做第二级
_IPC_OUTPUT_CAP = 250_000

_EXEC_FLAG = [False]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("codeact: 宿主连接关闭")
        buf.extend(chunk)
    return bytes(buf)


def _main() -> int:
    if len(sys.argv) < 2:
        return 2
    cfg_path = sys.argv[1]
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 帧编解码（同目录 result.py，按路径挂载，避开 app 包）
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("codeact_result", os.path.join(here, "result.py"))
    resmod = importlib.util.module_from_spec(spec)
    sys.modules["codeact_result"] = resmod  # dataclass 按 __module__ 查 sys.modules，必须先注册
    spec.loader.exec_module(resmod)  # type: ignore[union-attr]
    encode_frame, decode_frame, FrameCodecError = (
        resmod.encode_frame, resmod.decode_frame, resmod.FrameCodecError,
    )
    _head_tail = resmod._head_tail
    CodeActResult = resmod.CodeActResult

    sock = socket.create_connection(("127.0.0.1", int(cfg["port"])), timeout=15)
    sock.settimeout(None)  # 之后阻塞交给宿主看门狗/单 RPC 超时，socket 自身不再限时

    def send(payload: dict) -> None:
        sock.sendall(encode_frame(payload))

    def recv_frame() -> dict:
        header = _recv_exact(sock, 4)
        (size,) = struct.unpack(">I", header)
        return decode_frame(header + _recv_exact(sock, size))

    send({"ch": "hello", "token": cfg["token"], "pid": os.getpid()})
    ack = recv_frame()
    if not ack.get("ok"):
        return 3

    _preload(_IMPORT_WHITELIST)
    _evict(_EVICT_MODULES)

    builtins_dict = _safe_builtins()
    globals_ns = {
        "__builtins__": builtins_dict,
        "__name__": "__main__",
        "__file__": "<codeact>",
    }

    # api 模块（源码求值 → make_api 工厂 → 闭包注入 rpc/_b64）。
    # rpc 闭包持自增 id，宿主导调同步处理，无需按 id 匹配；id 仅用于审计/去重。
    api_src_ns: dict = {}
    exec(compile(cfg["sdk_source"], "<api>", "exec"), api_src_ns, api_src_ns)
    make_api = api_src_ns["make_api"]

    def rpc(fn: str, rid: int, args: dict):
        send({"ch": "rpc", "id": rid, "fn": fn, "args": args})
        resp = recv_frame()
        if resp.get("ok"):
            return resp.get("result")
        err = resp.get("error") or {}
        raise RuntimeError(str(err.get("msg") or f"codeact rpc '{fn}' failed"))

    api = types.ModuleType("api")
    api_funcs = make_api(rpc, __import__("base64"))
    for key, value in api_funcs.items():
        setattr(api, key, value)
    sys.modules["api"] = api
    globals_ns["api"] = api

    # [v2.1.1/P0-A] ready 帧：仅当 audit hook 就绪、api 构建完成才放行 RPC。
    # 宿主侧收到 ready 前不派发任何 RPC（窗口内无可利用执行面，见 session._ready）。
    send({"ch": "ready"})

    # audit hook + exec
    compiled = _compile_with_last_expr(cfg["code"])
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    tb, last_expr = None, None
    sys.addaudithook(_audit_hook)
    sys.stdout, sys.stderr = out, err
    _EXEC_FLAG[0] = True
    try:
        exec(compiled, globals_ns, globals_ns)
        if "__codeact_last_expr__" in globals_ns:
            try:
                # [C3] repr 先压到 IPC 上限，to_text 再做最终截断并标注
                last_expr = _head_tail(
                    repr(globals_ns["__codeact_last_expr__"]),
                    max(1, _IPC_OUTPUT_CAP // 2),
                )
            except Exception:  # noqa: BLE001
                last_expr = "<unprintable>"
    except BaseException as e:  # noqa: BLE001
        tb = "".join(traceback.format_exception(type(e), e, sys.exc_info()[2]))
    finally:
        _EXEC_FLAG[0] = False
        sys.stdout, sys.stderr = old_out, old_err

    stdout = _head_tail(out.getvalue(), _IPC_OUTPUT_CAP)
    stderr = _head_tail(err.getvalue(), _IPC_OUTPUT_CAP)
    if tb:
        tb = _head_tail(tb, _IPC_OUTPUT_CAP)
    result = CodeActResult(
        ok=tb is None,
        stdout=stdout,
        stderr=stderr,
        last_expr=last_expr,
        traceback=tb,
    )
    try:
        send(result.to_frame())
    except (ConnectionError, OSError, FrameCodecError):
        pass
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def _preload(whitelist: frozenset) -> None:
    """audit hook 之前预加载全部白名单模块，避免运行期触发内部 import 被误拦。"""
    import importlib
    for name in whitelist:
        try:
            importlib.import_module(name)
        except ImportError:
            pass


def _evict(modules: frozenset) -> None:
    """从 sys.modules 清理危险模块（尽力而为）。"""
    for name in modules:
        sys.modules.pop(name, None)


def _compile_with_last_expr(code: str):
    """把末尾顶层 Expr 改写为 __codeact_last_expr__ 赋值（设计 §8 last_expr）。"""
    tree = ast.parse(code, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        expr = tree.body.pop()
        assign = ast.Assign(
            targets=[ast.Name(id="__codeact_last_expr__", ctx=ast.Store())],
            value=expr.value,
        )
        ast.copy_location(assign, expr)
        ast.fix_missing_locations(assign)
        tree.body.append(assign)
    return compile(tree, "<codeact>", "exec")


def _safe_builtins() -> dict:
    real = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    names = {
        # 计算/类型
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "hash", "hex", "int", "isinstance",
        "issubclass", "iter", "len", "list", "map", "max", "min", "next",
        "object", "oct", "ord", "pow", "print", "range", "repr", "reversed",
        "round", "set", "slice", "sorted", "str", "sum", "super", "tuple",
        "type", "zip",
        # 常用异常
        "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
        "BufferError", "BytesWarning", "DeprecationWarning", "EOFError",
        "Exception", "FloatingPointError", "GeneratorExit", "ImportError",
        "ImportWarning", "IndentationError", "IndexError", "KeyError",
        "KeyboardInterrupt", "LookupError", "MemoryError", "NameError",
        "NotImplementedError", "OSError", "OverflowError", "ReferenceError",
        "RuntimeError", "RuntimeWarning", "StopIteration", "SyntaxError",
        "SyntaxWarning", "SystemError", "SystemExit", "TabError", "TypeError",
        "UnboundLocalError", "UnicodeDecodeError", "UnicodeEncodeError",
        "UnicodeError", "UnicodeTranslateError", "UnicodeWarning", "ValueError",
        "Warning", "ZeroDivisionError",
        # 特殊
        "__build_class__",
    }
    b = {n: real[n] for n in names if n in real}
    for n in ("ExceptionGroup", "BaseExceptionGroup"):
        if n in real:
            b[n] = real[n]
    b["__import__"] = _safe_import
    return b


_real_import = __import__


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = (name or "").split(".")[0]
    if root in _IMPORT_WHITELIST or root == "api":
        return _real_import(name, globals, locals, fromlist, level)
    raise ImportError(f"codeact: import '{name}' is not allowed")


def _audit_hook(event: str, args: tuple) -> None:
    if not _EXEC_FLAG[0]:
        return
    if event == "import":
        name = str(args[0]) if args else ""
        root = (name or "").split(".")[0]
        if root not in _IMPORT_ALLOWED_AUDIT:
            raise PermissionError(f"codeact: import '{name}' blocked")
        return
    if event.startswith("socket."):
        if event in _ALLOWED_SOCKET_AUDIT:
            return
        raise PermissionError(f"codeact: {event} blocked")
    if event in _BLOCKED_AUDIT:
        raise PermissionError(f"codeact: {event} blocked")


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except SystemExit:
        raise  # 正常退出码（含 0）直接上抛，不进错误分支
    except BaseException:  # noqa: BLE001
        # 与宿主握手/执行阶段崩溃：打印完整栈便于定位（宿主会把连接断开视为沙盒退出）
        traceback.print_exc()
        sys.exit(1)