"""CodeAct AST 静态守卫（设计 §4.1）。

拒绝即返回错误文本（不进沙盒）。这是「降低攻击面」层，不是安全边界；
真正边界是 §4.0 前置约束 + 可信用户/工作区 + 默认关闭 + builtins/hook/路径校验。

含 v2.1 修正：
  - dunder 属性拦截（ast.Attribute.attr 以 __ 开头结尾即拒）——堵
    `object.__subclasses__()` / `().__class__.__bases__` 类逃逸；
  - 导入白名单 + 黑名单；`from X import *` 拒绝；`from X import open` 拒绝。
"""

from __future__ import annotations

import ast
from typing import Optional

# 导入白名单（§4.1）：仅允许纯计算/纯数据处理模块
IMPORT_WHITELIST = frozenset({
    "math", "json", "re", "statistics", "datetime", "itertools", "functools",
    "collections", "decimal", "fractions", "random", "textwrap", "csv",
    "base64", "hashlib", "uuid", "unicodedata", "string",
})

# 导入黑名单：显式列出（便于审计/报错用语），语义上「白名单之外一律拒绝」
IMPORT_BLACKLIST = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib",
    "pathlib", "urllib", "http", "requests", "threading", "multiprocessing",
    "signal", "pty", "tempfile", "glob", "pickle", "marshal", "shelve",
    "dbm", "code", "codeop",
})

# 名/调用黑名单：出现在 Name / 函数调用 / from-import 目标即拒绝
NAME_BLACKLIST = frozenset({
    "eval", "exec", "compile", "__import__", "open", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "memoryview", "input",
    "breakpoint", "__builtins__",
})


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def analyze_code(
    code: str,
    max_code_chars: int = 20_000,
    max_ast_nodes: int = 5_000,
) -> Optional[str]:
    """静态分析代码，返回错误文本；通过则返回 None。

    由宿主在 fork 子进程前调用（设计「拒绝即返回错误，不进沙盒」）。
    """
    if code is None or not code.strip():
        return "codeact: 代码为空"
    if len(code) > max_code_chars:
        return (
            f"codeact: 代码过长（{len(code)} 字符 > 上限 {max_code_chars}），"
            "请浓缩逻辑或分批执行"
        )
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"codeact: 语法错误: {e}"
    except ValueError as e:  # NUL 字节等
        return f"codeact: 无法解析代码: {e}"

    nodes = 0
    for node in ast.walk(tree):
        nodes += 1
        if nodes > max_ast_nodes:
            return (
                f"codeact: AST 节点数超过上限 {max_ast_nodes}，"
                "代码过于复杂，请拆小后再试"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root not in IMPORT_WHITELIST:
                    return (
                        f"codeact: 禁止导入模块 '{alias.name}'\n"
                        f"（仅允许: {sorted(IMPORT_WHITELIST)}；"
                        "文件/网络/系统访问请走 api.*）"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in IMPORT_WHITELIST:
                return f"codeact: 禁止导入模块 '{node.module}'"
            for alias in node.names:
                if alias.name == "*":
                    return "codeact: 禁止 `from X import *`（无法逐名核对）"
                if alias.name in NAME_BLACKLIST:
                    return f"codeact: 禁止导入名字 '{alias.name}'"
        elif isinstance(node, ast.Name):
            if node.id in NAME_BLACKLIST:
                return f"codeact: 禁止使用内建名 '{node.id}'"
        elif isinstance(node, ast.Attribute):
            # v2.1：属性访问不受函数名黑名单约束，dunder 必须单独拦截
            if _is_dunder(node.attr):
                return (
                    f"codeact: 禁止访问特殊属性 '{node.attr}'"
                    f"（第 {node.lineno} 行）——反射逃逸通道一律封锁"
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in NAME_BLACKLIST:
                return f"codeact: 禁止调用 '{node.func.id}'"
    return None