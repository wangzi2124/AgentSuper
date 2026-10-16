"""沙盒内 `api` 模块源码生成（设计 §7）。

`build_api_source()` 返回的源码用 `exec` 求值得到 `make_api(rpc, _b64)` 工厂函数，
由 bootstrap 注入真实的 `rpc`（发帧+等回）与 `base64` 模块，返回的 api 函数是
闭包、**不含任何 `_rpc/_send_frame/_recv_frame` 全局**（P0/B3：用户代码即使绕过
dunder 拦截拿到 `__globals__`，也摸不到分发入口）。所有函数体只走 RPC 帧，
沙盒内**绝不**直接触碰文件系统/网络（那是宿主 _FILE_TOOLS / web_search 的职责）。
"""

from __future__ import annotations

_API_SOURCE = '''
"""codeact 沙盒内置 api（只读 + 受控写 + 网络 + 产物）。"""


def make_api(rpc, _b64):
    """注入 rpc 闭包后返回 api 函数集合（rpc: fn,_args->result, 错误抛 RuntimeError）。"""
    _next = {"n": 0}

    def _call(fn, **kw):
        _next["n"] += 1
        return rpc(fn, _next["n"], kw)

    def read_file(path, offset=0, limit=2000):
        """读取工作区文本文件（相对会话工作目录/绝对路径）。返回文件内容文本。"""
        return _call("read_file", path=path, offset=offset, limit=limit)

    def write_file(path, content, overwrite=False):
        """写入/新建文件（工作区内）。overwrite=False 时已存在会报错。"""
        return _call("write_file", path=path, content=content, overwrite=overwrite)

    def append_file(path, content):
        """向文件追加内容（不存在则创建）。"""
        return _call("append_file", path=path, content=content)

    def list_dir(path="."):
        """列出目录条目。"""
        return _call("list_dir", path=path)

    def glob(pattern, path="."):
        """按 glob 模式查找文件。"""
        return _call("glob", pattern=pattern, path=path)

    def grep(pattern, path=".", context=0, count_only=False, files_only=False):
        """在文件中按正则搜索。"""
        return _call(
            "grep", pattern=pattern, path=path,
            context=context, count_only=count_only, files_only=files_only,
        )

    def web_search(query, max_results=5):
        """联网搜索（受宿主 CODEACT_ALLOW_NETWORK 门控）。"""
        return _call("web_search", query=query, max_results=max_results)

    def save_artifact(name, data):
        """保存产物到 data/codeact/<session_id>/。data 为 base64 文本或 bytes。"""
        if isinstance(data, (bytes, bytearray)):
            data = _b64.b64encode(bytes(data)).decode("ascii")
        return _call("save_artifact", name=name, data=data)

    return {
        "read_file": read_file, "write_file": write_file, "append_file": append_file,
        "list_dir": list_dir, "glob": glob, "grep": grep,
        "web_search": web_search, "save_artifact": save_artifact,
    }
'''


def build_api_source() -> str:
    """返回注入沙盒的 api 模块源码（求值后得到 make_api 工厂，需注入 rpc/_b64）。"""
    return _API_SOURCE