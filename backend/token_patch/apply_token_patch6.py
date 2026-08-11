#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第六波补丁 v6：自定义工具（Skill 添加接口）—— 脚本型 + 固定型，配套按需挂载。

背景:
  第五波（v5）引入"工具 schema 按需挂载"后存在一个风险：未命中意图关键词的工具
  只出现在 system prompt 的工具名列表里、schema 不发模型。为消除该风险，本波新增
  「前端 Skill 添加接口」，让用户显式添加/固定工具：

  1. 脚本型（script）: 前端粘贴 Python 源码（含 tool_* 函数）→ 写入
     plugins/custom_<name>.py → 复用 PluginLoader 加载链路，tool_* 自动变为工具。
  2. 固定型（pin）  : 前端从工具目录选择已有工具名 → 写入 data/pinned_tools.json →
     按需挂载时该工具 schema 始终保留（_build_tool_defs 尊重 pinned 列表）。

本脚本 = v5（按需挂载，若未应用则先应用）+ v6 新增：
  - 新文件 app/skills/custom_tools.py : CustomToolStore（脚本文件 + pin 列表管理）
  - 新文件 app/api/custom_tools.py    : REST API（/api/custom-tools/*）
  - app/agent/graph.py : RAGAgent 接收 custom_tools；_build_tool_defs 尊重 pinned
  - app/runtime.py     : 创建 CustomToolStore 并注入 agent / app.state
  - app/main.py        : 挂载 /api/custom-tools 路由

用法（必须按顺序，先跑过 PATCH1-4 再跑本脚本）:
    python token_patch/apply_token_patch6.py            # 应用（自动先补 v5）
    python token_patch/apply_token_patch6.py --verify   # 校验
    python token_patch/apply_token_patch6.py --rollback # 回滚（v6 先回滚，v5 再回滚）

安全性:
  - 每个替换前 count 校验：0 次=MISS（已应用或版本不符），>1 次=SKIP，均不碰文件
  - 应用前自动备份 *.bak_token_patch6（v5 的备份是 *.bak_token_patch5）
  - 新增文件带幂等保护：已存在则 SKIP 不覆盖
  - 写操作接口均 require_admin 保护；脚本型文件名做净化防路径穿越
"""
import argparse
import importlib
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_patch6"

# ── 复用 v5（按需挂载）──
_PATCH5_MODULE = "apply_token_patch5"
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    _p5 = importlib.import_module(_PATCH5_MODULE)
    PATCHES_V5 = _p5.PATCHES
    _HAS_P5 = True
except Exception as _e:  # noqa: BLE001
    PATCHES_V5 = []
    _HAS_P5 = False
    print("[WARN] 无法加载 %s.py（%s），v6 将跳过 v5 补丁；请先单独运行 v5。" % (_PATCH5_MODULE, _e))

# ── v6 新增文件（幂等：已存在则 SKIP）──
NEW_FILES: list[tuple[str, str]] = []

_CUSTOM_TOOLS_PY = r'''# -*- coding: utf-8 -*-
"""自定义工具存储 [token 优化 v6]。

前端「Skills → 自定义工具」页面的后端支撑:
  - 脚本型（script）: 用户粘贴的 Python 源码写入 plugins/custom_<name>.py,
    复用 PluginLoader 的加载/启停/执行链路（tool_* 函数自动变为工具）。
  - 固定型（pin）  : 用户从前端工具目录中选择已有工具名，写入 data/pinned_tools.json;
    按需挂载（v5）时这些工具始终挂载 schema，解决"模型看不到工具"的顾虑。
"""
import json
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
    """自定义工具存储：管理脚本型（插件文件）与固定型（pin 列表）。"""

    def __init__(self, plugins_dir: str = "plugins", pinned_path: str = "data/pinned_tools.json"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.pinned_path = Path(pinned_path)
        self.pinned_path.parent.mkdir(parents=True, exist_ok=True)

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

    # ── 固定型 ──
    def _load_pins(self) -> list:
        try:
            return json.loads(self.pinned_path.read_text(encoding="utf-8")).get("pins", [])
        except Exception:
            return []

    def _save_pins(self, pins: list) -> None:
        self.pinned_path.write_text(
            json.dumps({"pins": pins}, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create_pin(self, tool_name: str, description: str = "") -> dict:
        pins = self._load_pins()
        if any(p["tool_name"] == tool_name for p in pins):
            raise ValueError(f"工具 {tool_name} 已在固定列表中")
        pin = {"tool_name": tool_name, "description": description or tool_name, "enabled": True}
        pins.append(pin)
        self._save_pins(pins)
        return pin

    def pinned_tools(self, enabled_only: bool = True) -> List[str]:
        pins = self._load_pins()
        if enabled_only:
            pins = [p for p in pins if p.get("enabled", True)]
        return [p["tool_name"] for p in pins]

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
        for pin in self._load_pins():
            items.append({
                "name": pin["tool_name"],
                "type": "pin",
                "description": pin.get("description", pin["tool_name"]),
                "path": str(self.pinned_path),
                "enabled": pin.get("enabled", True),
                "tools": [pin["tool_name"]],
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
        pins = self._load_pins()
        for pin in pins:
            if pin["tool_name"] == name:
                pin["enabled"] = enabled
                self._save_pins(pins)
                return True
        return False

    def remove(self, name: str) -> bool:
        f = self.script_file(name)
        if f:
            f.unlink(missing_ok=True)
            (self.plugins_dir / f"{f.stem}.enabled").unlink(missing_ok=True)
            return True
        pins = self._load_pins()
        kept = [p for p in pins if p["tool_name"] != name]
        if len(kept) != len(pins):
            self._save_pins(kept)
            return True
        return False
'''

_CUSTOM_TOOLS_API_PY = r'''# -*- coding: utf-8 -*-
"""自定义工具 API [token 优化 v6]。

前端「Skills → 自定义工具」页面的后端接口：
  - 脚本型：粘贴 Python 源码（含 tool_* 函数）→ 写入 plugins/custom_*.py → 热加载
  - 固定型：把已有工具 pin 到常驻列表（按需挂载时始终挂载其 schema）
所有写操作后都会 reload 插件并 refresh_tools（与 skills toggle 同一链路）。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class ScriptRequest(BaseModel):
    name: str
    description: str = ""
    script: str
    enabled: bool = True


class PinRequest(BaseModel):
    tool_name: str
    description: str = ""


class ToggleRequest(BaseModel):
    enabled: bool


def _store(request: Request):
    store = getattr(request.app.state, "custom_tools", None)
    if store is None:
        raise HTTPException(status_code=500, detail="custom_tools store not initialized")
    return store


async def _reload(request: Request) -> None:
    """热加载：重新扫描插件 + 刷新 agent 工具（与 skills toggle 相同链路）。"""
    try:
        request.app.state.plugin_loader.load_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("plugin reload failed: %s", e)
    try:
        await request.app.state.agent.refresh_tools()
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh_tools failed: %s", e)


@router.get("/")
async def list_custom_tools(request: Request):
    """列出所有自定义工具（脚本型 + 固定型）。"""
    return _store(request).list()


@router.get("/catalog")
async def tool_catalog(request: Request):
    """返回当前所有可用工具目录（供前端「固定已有工具」下拉选择）。"""
    names: list[dict] = []
    agent = getattr(request.app.state, "agent", None)
    if agent is not None:
        for t in getattr(agent, "tools", []):
            names.append({
                "name": t.name,
                "description": (getattr(t, "description", "") or "")[:120],
            })
    return names


@router.post("/script")
async def create_script(body: ScriptRequest, request: Request):
    """创建脚本型自定义工具（写入 plugins/custom_*.py 并热加载）。"""
    require_admin(request)
    store = _store(request)
    try:
        item = store.create_script(body.name.strip(), body.description.strip(), body.script, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload(request)
    return item


@router.post("/pin")
async def create_pin(body: PinRequest, request: Request):
    """固定一个已有工具：按需挂载时始终保留其 schema。"""
    require_admin(request)
    store = _store(request)
    catalog = {t["name"] for t in await tool_catalog(request)}
    if body.tool_name.strip() not in catalog:
        raise HTTPException(status_code=400, detail=f"工具不存在: {body.tool_name}")
    try:
        pin = store.create_pin(body.tool_name.strip(), body.description.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload(request)
    return pin


@router.post("/{name}/toggle")
async def toggle_custom(name: str, body: ToggleRequest, request: Request):
    """启用/禁用自定义工具（脚本型改 .enabled 文件，固定型改 pins 列表）。"""
    require_admin(request)
    store = _store(request)
    if not store.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.delete("/{name}")
async def delete_custom(name: str, request: Request):
    """删除自定义工具（脚本型删文件，固定型删 pin 条目）。"""
    require_admin(request)
    store = _store(request)
    if not store.remove(name):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' deleted"}
'''

NEW_FILES = [
    ("app/skills/custom_tools.py", _CUSTOM_TOOLS_PY),
    ("app/api/custom_tools.py", _CUSTOM_TOOLS_API_PY),
]

# ── v6 文件修改（基于 v5 应用后的源码）──
PATCHES_V6 = [
    # ── V6-1. graph.py: 顶部 import CustomToolStore ──
    (
        "app/agent/graph.py",
        '''from app.agent.tools import (
    ToolDef,
    LONG_CONTENT_FILE_RULE,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)''',
        '''from app.agent.tools import (
    ToolDef,
    LONG_CONTENT_FILE_RULE,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)
from app.skills.custom_tools import CustomToolStore  # [token 优化 v6]''',
        "V6-1 graph.py import CustomToolStore",
    ),
    # ── V6-2. graph.py: __init__ 签名加 custom_tools 参数 ──
    (
        "app/agent/graph.py",
        '''    def __init__(
        self,
        retriever: Retriever,
        skill_loader: SkillLoader | None = None,
        plugin_loader: PluginLoader | None = None,
        reranker: Reranker | None = None,
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader''',
        '''    def __init__(
        self,
        retriever: Retriever,
        skill_loader: SkillLoader | None = None,
        plugin_loader: PluginLoader | None = None,
        reranker: Reranker | None = None,
        custom_tools: CustomToolStore | None = None,  # [token 优化 v6] 前端添加的自定义工具/固定工具
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.custom_tools = custom_tools''',
        "V6-2 graph.py __init__ 接收 custom_tools",
    ),
    # ── V6-3. graph.py: _build_tool_defs 尊重 pinned（基于 v5 的按需挂载版本）──
    (
        "app/agent/graph.py",
        '''        used = used_names or set()
        q = (question or "").lower()
        selected: list[ToolDef] = []
        for t in self.tools:
            if t.name in used:
                selected.append(t)
                continue''',
        '''        used = used_names or set()
        q = (question or "").lower()
        # [token 优化 v6] 固定（pin）工具集合只读一次，避免循环内反复读 pinned_tools.json
        pinned = self._pinned_tool_names()
        selected: list[ToolDef] = []
        for t in self.tools:
            # [token 优化 v6] 前端固定（pin）的工具始终挂载，不受意图筛选影响
            if t.name in pinned:
                selected.append(t)
                continue
            if t.name in used:
                selected.append(t)
                continue''',
        "V6-3 _build_tool_defs 尊重 pinned 列表",
    ),
    # ── V6-4. graph.py: 新增 _pinned_tool_names 方法 ──
    (
        "app/agent/graph.py",
        '''        return [t.to_openai_tool() for t in selected]

    def _bound_plugin_result(self, name: str, result: str) -> str:''',
        '''        return [t.to_openai_tool() for t in selected]

    def _pinned_tool_names(self) -> set[str]:
        """[token 优化 v6] 返回前端固定（pin）的工具名集合（始终挂载 schema）。"""
        try:
            if self.custom_tools:
                return set(self.custom_tools.pinned_tools())
        except Exception:
            pass
        return set()

    def _bound_plugin_result(self, name: str, result: str) -> str:''',
        "V6-4 graph.py 新增 _pinned_tool_names",
    ),
    # ── V6-5. runtime.py: 创建 CustomToolStore（须在 _data_dir 定义之后，否则 NameError） ──
    (
        "app/runtime.py",
        '''    _base_dir = Path(__file__).resolve().parents[1]
    _data_dir = _base_dir / "data"
    perm_mgr = PermissionManager(''',
        '''    _base_dir = Path(__file__).resolve().parents[1]
    _data_dir = _base_dir / "data"

    # [token 优化 v6] 自定义工具存储：脚本型写 plugins/custom_*.py（复用插件加载链路），
    # 固定型（pin）写 data/pinned_tools.json（按需挂载时始终保留该工具 schema）
    from app.skills.custom_tools import CustomToolStore
    custom_tools = CustomToolStore(
        plugins_dir=settings.plugins_dir,
        pinned_path=str(_data_dir / "pinned_tools.json"),
    )

    perm_mgr = PermissionManager(''',
        "V6-5 runtime.py 创建 CustomToolStore",
    ),
    # ── V6-6. runtime.py: 注入 agent ──
    (
        "app/runtime.py",
        "    agent = RAGAgent(retriever, skill_loader, plugin_loader, reranker=reranker)",
        "    agent = RAGAgent(retriever, skill_loader, plugin_loader, reranker=reranker, custom_tools=custom_tools)",
        "V6-6 runtime.py 注入 agent",
    ),
    # ── V6-7. runtime.py: 挂到 app.state ──
    (
        "app/runtime.py",
        '''    app.state.skill_loader = skill_loader
    app.state.plugin_loader = plugin_loader''',
        '''    app.state.skill_loader = skill_loader
    app.state.plugin_loader = plugin_loader
    app.state.custom_tools = custom_tools''',
        "V6-7 runtime.py 挂到 app.state",
    ),
    # ── V6-8. main.py: import 路由 ──
    (
        "main.py",
        "from app.api import documents, chat, skills, plugins, vectors, generated, permission as perm_api, config, weather, auth as auth_api",
        "from app.api import documents, chat, skills, plugins, vectors, generated, permission as perm_api, config, weather, auth as auth_api, custom_tools as custom_tools_api",
        "V6-8 main.py import custom_tools 路由",
    ),
    # ── V6-9. main.py: include_router ──
    (
        "main.py",
        'app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])',
        'app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])\napp.include_router(custom_tools_api.router, prefix="/api/custom-tools", tags=["Custom Tools"])',
        "V6-9 main.py 挂载 /api/custom-tools",
    ),
]


def _target_path(rel: str) -> Path:
    return BACKEND_ROOT / rel


def _read(rel: str) -> str:
    return _target_path(rel).read_text(encoding="utf-8")


def _write(rel: str, text: str) -> None:
    _target_path(rel).write_text(text, encoding="utf-8")


def _backup(rel: str) -> Path:
    bak = _target_path(rel + BAK_SUFFIX)
    if not bak.exists():
        shutil.copy2(_target_path(rel), bak)
    return bak


def _apply_patches(patch_list: list, tag: str) -> tuple[int, int, int]:
    ok = miss = skip = 0
    for rel, old, new, desc in patch_list:
        try:
            text = _read(rel)
        except FileNotFoundError:
            print("[MISS] %s 文件不存在，跳过" % rel)
            miss += 1
            continue
        cnt = text.count(old)
        if cnt == 0:
            print("[MISS] %s | %s —— 未找到（已应用或版本不同）" % (rel, desc))
            miss += 1
            continue
        if cnt > 1:
            print("[SKIP] %s | %s —— 匹配 %d 处，歧义，不碰文件" % (rel, desc, cnt))
            skip += 1
            continue
        _backup(rel)
        _write(rel, text.replace(old, new, 1))
        print("[ OK ] %s | %s" % (rel, desc))
        ok += 1
    print("-- %s 子项: OK=%d MISS=%d SKIP=%d" % (tag, ok, miss, skip))
    return ok, miss, skip


def _apply_new_files() -> tuple[int, int]:
    ok = skip = 0
    for rel, content in NEW_FILES:
        path = _target_path(rel)
        if path.exists():
            print("[SKIP] %s 已存在，不覆盖" % rel)
            skip += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print("[ OK ] 新文件 %s (%d 字节)" % (rel, len(content)))
        ok += 1
    return ok, skip


def apply() -> None:
    print("=" * 70)
    print("PATCH6 应用开始")
    print("=" * 70)

    # 0) 先补 v5（按需挂载），v6 的 pinned 集成基于 v5 后的源码
    if _HAS_P5 and PATCHES_V5:
        print("\n[步骤 0] 先确保 v5（按需挂载）已应用……")
        _apply_patches(PATCHES_V5, "v5")

    # 1) 新文件
    print("\n[步骤 1] 创建新文件……")
    _apply_new_files()

    # 2) 修改现有文件
    print("\n[步骤 2] 修改现有文件……")
    _apply_patches(PATCHES_V6, "v6")

    print("-" * 70)
    print("PATCH6 完成。请重启后端生效。")
    print("备份: *.%s （回滚: python %s --rollback）" % (BAK_SUFFIX.lstrip("."), Path(__file__).name))


def verify() -> None:
    print("=" * 70)
    print("PATCH6 校验")
    print("=" * 70)
    ok = miss = 0
    # 新文件
    for rel, _content in NEW_FILES:
        if _target_path(rel).exists():
            print("[ OK ] 新文件 %s 存在" % rel)
            ok += 1
        else:
            print("[MISS] 新文件 %s 不存在" % rel)
            miss += 1
    # 修改项（注意：部分 v6 补丁是追加式 new = old + 新增行，old 应用后仍在文件中，
    # 因此先看 new 是否已存在，其次才看 old 是否被整体替换）
    for rel, old, new, desc in PATCHES_V6:
        try:
            text = _read(rel)
        except FileNotFoundError:
            print("[MISS] %s 文件不存在" % rel)
            miss += 1
            continue
        if new in text:
            print("[ OK ] %s | %s" % (rel, desc))
            ok += 1
        elif old not in text:
            print("[ OK ] %s | %s" % (rel, desc))
            ok += 1
        else:
            print("[MISS] %s | %s —— new 未出现且 old 仍存在（未应用）" % (rel, desc))
            miss += 1
    print("-" * 70)
    print("结果: OK=%d  MISS=%d" % (ok, miss))
    if _HAS_P5:
        print("\n（v5 部分请用 apply_token_patch5.py --verify 单独校验）")


def rollback() -> None:
    print("=" * 70)
    print("PATCH6 回滚（先 v6 后 v5，顺序与应用相反）")
    print("=" * 70)
    for rel, _content in NEW_FILES:
        p = _target_path(rel)
        if p.exists():
            p.unlink()
            print("[ OK ] 删除新文件 %s" % rel)
    rels = sorted({rel for rel, _, _, _ in PATCHES_V6})
    for rel in rels:
        bak = _target_path(rel + BAK_SUFFIX)
        if not bak.exists():
            print("[SKIP] %s 无 v6 备份" % rel)
            continue
        shutil.copy2(bak, _target_path(rel))
        print("[ OK ] %s 已恢复 v6 备份" % rel)
    if _HAS_P5:
        print("\n正在回滚 v5（apply_token_patch5.py --rollback）……")
        _p5.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description="第六波补丁 v6：自定义工具（Skill 添加接口）")
    parser.add_argument("--verify", action="store_true", help="仅校验是否已应用")
    parser.add_argument("--rollback", action="store_true", help="回滚到备份")
    args = parser.parse_args()
    if args.rollback:
        rollback()
        return
    if args.verify:
        verify()
        return
    apply()


if __name__ == "__main__":
    main()
