"""工具结果信封（对齐 opencode Tool.execute 的 {title, metadata, output}）。

中性实现：新核心（graph 节点、execution 工具）直接使用，不依赖 `app.*`。
`envelope` 构造信封；`unwrap` 从信封提取 output 字符串（非信封直接 str）。
"""

from __future__ import annotations


def envelope(title: str, output: str, **metadata) -> dict:
    """构造工具结果信封 {title, metadata, output}。"""
    return {"title": title, "metadata": metadata, "output": output}


def unwrap(result: object) -> str:
    """从信封结构提取 output 字符串；非信封直接 str()（兼容旧返回）。"""
    if isinstance(result, dict) and "output" in result:
        return str(result["output"])
    return str(result)


__all__ = ["envelope", "unwrap"]
