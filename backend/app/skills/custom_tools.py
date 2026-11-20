# -*- coding: utf-8 -*-
"""自定义工具存储。

前端「自定义工具」页面的后端支撑：
用户粘贴的 Python 源码写入 plugins/custom_<name>.py，
复用 PluginLoader 的加载/启停/执行链路（tool_* 函数自动变为工具）。
"""
import logging
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SCRIPT_PREFIX = "custom_"

# 脚本型插件的安全壳：用户脚本缺失元信息时自动补全
# 注意：外层 _CUSTOM_TOOLS_PY 是 r 前缀的 三单引号 原始字符串，此处内部模板必须用 三双引号 定界，
# 且内层 docstring 的双引号需写成转义形式，才能在写出后的文件里被解析为真正的 三双引号。
_TEMPLATE = """# -*- coding: utf-8 -*-
\"\"\"{description}\"\"\"
# 本文件由前端「自定义工具」页面生成 [token 优化 v6]
PLUGIN_NAME = "{name}"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "{description}"

{script}
"""


class CustomToolStore:
    """自定义工具存储：管理脚本型插件文件。"""

    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    # ── 名称净化（防路径穿越 / 非法文件名）──
    @staticmethod
    def _slug(name: str) -> str:
        s = re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "").strip()).strip("._-")
        if not s or s in ("..", ".") or s.startswith("_"):
            raise ValueError("非法工具名: %r" % name)
        return s

    # ── 脚本型 ──
    def create_script(self, name: str, description: str, script: str, enabled: bool = True) -> dict:
        slug = self._slug(name)
        if "tool_" not in script:
            raise ValueError("脚本中至少需要一个 tool_* 函数（插件加载器据此生成工具）")
        path = self.plugins_dir / f"{_SCRIPT_PREFIX}{slug}.py"
        if path.exists():
            raise ValueError(f"自定义工具 {name} 已存在（{path.name}）")
        # 用 .replace 链代替 .format：用户脚本里可能含 {}（dict/f-string），
        # .format 会抛 ValueError；{script} 最后替换，脚本内容不再被扫描。
        code = (
            _TEMPLATE.replace("{name}", slug)
            .replace("{description}", (description or name).replace('"', "'"))
            .replace("{script}", script)
        )
        path.write_text(code, encoding="utf-8")
        if enabled:
            (self.plugins_dir / f"{_SCRIPT_PREFIX}{slug}.enabled").touch()
        return self.get(slug) or {
            "name": slug, "type": "script", "description": description or name,
            "path": str(path), "enabled": enabled, "tools": self._script_tools(code),
        }

    @staticmethod
    def _script_tools(code: str) -> List[str]:
        return re.findall(r"^def\s+(tool_\w+)\s*\(", code, re.MULTILINE)

    def script_file(self, name: str) -> Optional[Path]:
        slug = self._slug(name)
        p = self.plugins_dir / f"{_SCRIPT_PREFIX}{slug}.py"
        return p if p.exists() else None

    # ── 统一管理 ──
    def list(self) -> List[dict]:
        items: List[dict] = []
        for p in sorted(self.plugins_dir.glob(f"{_SCRIPT_PREFIX}*.py")):
            code = p.read_text(encoding="utf-8")
            enabled = (self.plugins_dir / f"{p.stem}.enabled").exists()
            name = p.stem[len(_SCRIPT_PREFIX):]
            m = re.search(r'PLUGIN_DESCRIPTION = "([^"]*)"', code)
            items.append({
                "name": name,
                "type": "script",
                "description": m.group(1) if m else name,
                "path": str(p),
                "enabled": enabled,
                "tools": self._script_tools(code),
            })
        return items

    def get(self, name: str) -> Optional[dict]:
        for item in self.list():
            if item["name"] == name:
                return item
        return None

    def toggle(self, name: str, enabled: bool) -> bool:
        f = self.script_file(name)
        if f:
            ef = self.plugins_dir / f"{f.stem}.enabled"
            if enabled:
                ef.touch()
            else:
                ef.unlink(missing_ok=True)
            return True
        return False

    def remove(self, name: str) -> bool:
        f = self.script_file(name)
        if f:
            f.unlink(missing_ok=True)
            (self.plugins_dir / f"{f.stem}.enabled").unlink(missing_ok=True)
            return True
        return False
