"""生成文件输出路径校验工具（供 docx/pdf/excel/pptx 生成插件复用）。

文件名以 `_` 开头，PluginLoader 不会把它当作插件加载。
"""

from pathlib import Path

GENERATED_DIR = Path(__file__).resolve().parents[1] / "data" / "generated"


def resolve_output_path(output_path: str, extension: str) -> Path:
    """解析并校验生成文件的输出路径，确保始终位于 data/generated/ 内。

    - output_path 相对路径：基于 GENERATED_DIR 解析
    - output_path 绝对路径：必须位于 GENERATED_DIR 内，否则抛 ValueError
    - 缺扩展名时自动补 extension
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if not output_path:
        raise ValueError("output_path is required; provide a filename")
    p = Path(output_path)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (GENERATED_DIR / p).resolve()
    if not resolved.is_relative_to(GENERATED_DIR.resolve()):
        raise ValueError(
            f"output_path must be inside {GENERATED_DIR}, got: {output_path}"
        )
    if not resolved.name.lower().endswith(extension):
        resolved = resolved.with_name(resolved.name + extension)
    return resolved
