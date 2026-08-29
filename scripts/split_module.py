# -*- coding: utf-8 -*-
"""模块拆分器：把超大模块按主题机械拆成子包（AST 行切片移动，杜绝转录错误）。

用法: python scripts/split_module.py <key>
支持 key: file_tools / chat / graph（configs 在文件底部）。

产出:
  - <parent>/<sublib>/<mod>.py   各子模块（自动补跨模块 import + 前置注释 + __all__）
  - 巨型类（RAGAgent 等）按 cfg["class_split"] 切成继承链：每个切片是独立模块，
    后一块类继承前一块类（方法经 self/MRO 互通）。
  - 原 <name>.py 重写为 facade：from .<sublib>.<mod> import *
现有全部 import 路径与符号保持不变，行为零变化。
文件读写全部用 Python 显式 utf-8，规避 PowerShell 编码问题。
"""
import argparse
import ast
import json
import os
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if isinstance(__builtins__, dict):
    _BUILTINS = set(__builtins__)
else:
    _BUILTINS = set(dir(__builtins__))


# ---------------------------------------------------------------------------
# 绑定/使用分析（词法作用域）
# ---------------------------------------------------------------------------

class _BindingWalker(ast.NodeVisitor):
    """收集 used（需模块级解析的名字）。

    - def/lambda/class body/推导式 → push 新作用域（形参/类名/推导目标入内）
    - `global x` → x 强制计入 used（永不豁免）
    - 赋值目标/导入别名/for/with/except/walrus → 在所在作用域绑定
    - 装饰器/参数默认值/types - 在函数体之前（外层作用域）求值
    - 推导式：iter_i 在前序生成器已绑定的作用域求值；元素在最内层
    """

    def __init__(self):
        self.stack = []
        self.used = set()
        self.global_forced = set()

    def _push(self, bound):
        self.stack.append(set(bound))

    def _pop(self):
        self.stack.pop()

    def _bound(self, names):
        if names and self.stack:
            self.stack[-1].update(names)

    def _mark_used(self, name):
        if name in _BUILTINS:
            return
        if name in self.global_forced:
            self.used.add(name)
            return
        for s in reversed(self.stack):
            if name in s:
                return
        self.used.add(name)

    def _visit_callable(self, node):
        args = node.args
        params = [a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)]
        if args.vararg:
            params.append(args.vararg.arg)
        if args.kwarg:
            params.append(args.kwarg.arg)
        for d in node.decorator_list:
            self.visit(d)
        for d in args.defaults:
            self.visit(d)
        for d in args.kw_defaults:
            if d is not None:
                self.visit(d)
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            if a.annotation is not None:
                self.visit(a.annotation)
        if args.vararg and args.vararg.annotation is not None:
            self.visit(args.vararg.annotation)
        if args.kwarg and args.kwarg.annotation is not None:
            self.visit(args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self._push(params + [node.name])
        for s in node.body:
            self.visit(s)
        self._pop()

    def visit_FunctionDef(self, node):
        self._visit_callable(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_callable(node)

    def visit_Lambda(self, node):
        args = node.args
        params = [a.arg for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)]
        if args.vararg:
            params.append(args.vararg.arg)
        if args.kwarg:
            params.append(args.kwarg.arg)
        self._push(params)
        self.visit(node.body)
        self._pop()

    def visit_ClassDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for k in node.keywords:
            self.visit(k)
        self._push([])
        for s in node.body:
            self.visit(s)
        self._pop()

    def visit_Global(self, node):
        for n in node.names:
            self.global_forced.add(n)

    def visit_Assign(self, node):
        self.visit(node.value)
        self._bound(self._coll_names(node.targets))

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
        if node.annotation is not None:
            self.visit(node.annotation)
        if isinstance(node.target, ast.Name):
            self._bound([node.target.id])

    def visit_AugAssign(self, node):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._bound([node.target.id])

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._bound([node.target.id])

    def _coll_names(self, targets):
        out = []
        for t in targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                out.extend(self._coll_names(t.elts))
        return out

    def _visit_targets(self, tgt):
        self._bound(self._coll_names([tgt]))

    def visit_For(self, node):
        self.visit(node.iter)
        self._visit_targets(node.target)
        for s in node.body + node.orelse:
            self.visit(s)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            if item.context_expr is not None:
                self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._visit_targets(item.optional_vars)
        for s in node.body:
            self.visit(s)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bound([node.name])
        for s in node.body:
            self.visit(s)

    def visit_Import(self, node):
        self._bound([a.asname or a.name.split('.')[0] for a in node.names])

    def visit_ImportFrom(self, node):
        self._bound([a.asname or a.name for a in node.names])

    def visit_Name(self, node):
        self._mark_used(node.id)

    def visit_ListComp(self, node):
        self._comp(node, lambda: self.visit(node.elt))

    def visit_SetComp(self, node):
        self._comp(node, lambda: self.visit(node.elt))

    def visit_GeneratorExp(self, node):
        self._comp(node, lambda: self.visit(node.elt))

    def visit_DictComp(self, node):
        self._comp(node, lambda: (self.visit(node.key), self.visit(node.value)))

    def _comp(self, node, elt_visitor):
        self._push([])
        for g in node.generators:
            self.visit(g.iter)
            self._visit_targets(g.target)
            for c in g.ifs:
                self.visit(c)
        elt_visitor()
        self._pop()


def collect_used(node):
    w = _BindingWalker()
    w.visit(node)
    return w.used


# ---------------------------------------------------------------------------
# 顶层切分 + 行切片
# ---------------------------------------------------------------------------

def split_top_level(tree):
    imports, statements, module_doc = [], [], ""
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        module_doc = body[0].value.value
        body = body[1:]
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            if getattr(node, "decorator_list", None):
                start = min(d.lineno for d in node.decorator_list)
            statements.append(([node.name], node, start))
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else ([node.target.id] if isinstance(node.target, ast.Name) else [])
            if names:
                statements.append((names, node, node.lineno))
                continue
        raise SystemExit(f"[splitter] 未处理顶层语句 @L{getattr(node, 'lineno', '?')}: {type(node).__name__}")
    return imports, statements, module_doc


def slice_lines(src_lines, a, b):
    """[a,b) 行区间（1-based 半开）→ 原文（含内部注释）。"""
    return "\n".join(src_lines[a - 1:b - 1]) if b > a else ""


def lines_len(node):
    return node.end_lineno - node.lineno + 1


def pre_comment(src_lines, prev_end, start):
    if prev_end + 1 >= start:
        return ""
    raw = slice_lines(src_lines, prev_end + 1, start)
    return raw if raw.strip() else ""


# ---------------------------------------------------------------------------
# 类体切分
# ---------------------------------------------------------------------------

def _class_docstring_end(cls_node):
    if cls_node.body and isinstance(cls_node.body[0], ast.Expr) and isinstance(getattr(cls_node.body[0], "value", None), ast.Constant) and isinstance(cls_node.body[0].value.value, str):
        return cls_node.body[0].end_lineno
    return None


def build_class_chunks(cls_node, spec, src_lines):
    """给巨型类分配方法/类级语句到各继承块。返回 (chunk_idx, class_name, base_class, members列表[含前置注释])。"""
    body_nodes = cls_node.body
    func_nodes = {}
    for n in body_nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes[n.name] = n

    def _name_of(n):
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            return n.targets[0].id
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            return n.target.id
        return None

    chunks = spec["chunks"]
    assigned = {}
    for ci, ch in enumerate(chunks):
        for mname in ch.get("members", []):
            if mname not in func_nodes:
                raise SystemExit(f"[splitter] 类 {cls_node.name} 缺成员 {mname}")
            assigned[mname] = ci
        for rng in ch.get("ranges", []):
            lo, hi = rng
            for n in body_nodes:
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.lineno >= lo and n.end_lineno <= hi:
                    name = _name_of(n)
                    if name:
                        assigned[name] = ci
    # 类级其余语句（Assign/AnnAssign）默认归第 0 块
    for n in body_nodes:
        name = _name_of(n)
        if name and name not in assigned:
            assigned[name] = 0
    for mname in func_nodes:
        if mname not in assigned:
            raise SystemExit(f"[splitter] 类 {cls_node.name} 方法 {mname} 未分配")
    doc_end = _class_docstring_end(cls_node) or cls_node.lineno

    # 全类成员按源码顺序排（含各自块号）
    global_members = []
    for n in body_nodes:
        ci = None
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ci = assigned[n.name]
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            name = _name_of(n)
            if name:
                ci = assigned.get(name)
        if ci is not None:
            global_members.append((n, ci))
    global_members.sort(key=lambda p: p[0].lineno)

    result = []
    for i, ch in enumerate(spec["chunks"]):
        base = spec["chunks"][i - 1]["class"] if i > 0 else None
        result.append({"class": ch["class"], "base": base, "members": []})
    last_end = doc_end
    for n, ci in global_members:
        start = n.lineno
        if getattr(n, "decorator_list", None):
            start = min(d.lineno for d in n.decorator_list)
        pre = pre_comment(src_lines, last_end, start)
        result[ci]["members"].append((pre, _source_lines(src_lines, n.lineno, n.end_lineno)))
        last_end = max(last_end, n.end_lineno)
    return result


def _source_lines(src_lines, a, b):
    return "\n".join(src_lines[a - 1:b])


# ---------------------------------------------------------------------------
# 模块文档 + 生成
# ---------------------------------------------------------------------------

def _module_docstring(mod_name, names, original_doc):
    doc = (original_doc or "(无)").strip().replace('"""', "'''")
    head = "、".join(names) if names else "(类分块)"
    return f'"""拆分模块 `{mod_name}`（含 {head}）。\n\n原文件 docstring: {doc}"""'


def build_header(imports, src_lines):
    header_imported = set()
    for imp in imports:
        header_imported.update(
            a.asname or a.name.split('.')[0] for a in imp.names
            if not (isinstance(imp, ast.ImportFrom) and imp.module and imp.module.startswith('.'))
        )
    header_src = "\n\n".join(slice_lines(src_lines, i.lineno, i.end_lineno + 1) for i in imports)
    return header_imported, header_src


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def split(cfg):
    src = os.path.join(ROOT, cfg["src"])
    parent = os.path.dirname(src)
    sublib = cfg["sublib"]
    subdir = os.path.join(parent, sublib)
    with open(src, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text[:4000] else "\n"
    src_lines = text.split(nl)
    tree = ast.parse(text)
    imports, statements, module_doc = split_top_level(tree)

    per_module = cfg.get("per_module_consts", {})
    per_module_src = "\n".join(f"{k} = {v}" for k, v in per_module.items())
    class_names = set(cfg.get("class_split", {}))
    cls_nodes = {}
    reg_stmts = []
    for names, node, start in statements:
        if names[0] in class_names:
            cls_nodes[names[0]] = node
        else:
            reg_stmts.append((names, node, start))

    # ---- 校验顶层定义 ----
    used_by = {n: m for m, names_ in cfg["modules"].items() for n in names_}
    for names, node, _s in reg_stmts:
        for n in names:
            if n not in used_by and n not in per_module:
                raise SystemExit(f"[{cfg['src']}] 顶层定义名 {n} 未分配模块")
    for cname in cls_nodes:
        if cname not in class_names:
            raise SystemExit(f"[{cfg['src']}] 类 {cname} 未在 class_split 中配置")

    header_imported, header_src = build_header(imports, src_lines)
    all_regular_defs = set(used_by)

    # ---- 常规语句分块 ----
    blocks = build_blocks(src_lines, reg_stmts, per_module)
    mod_nodes = {m: [] for m in cfg["modules"]}
    for b in blocks:
        mod_nodes[used_by[b[0][0]]].append(b)
    mod_defs = {m: set(names) for m, names in cfg["modules"].items()}
    mod_used = {m: set(per_module.keys()) for m in cfg["modules"]}
    for b in blocks:
        for names, pre, body in [b]:
            mod_used[used_by[names[0]]].update(collect_used_from_str(body))

    cross = {}
    all_mods = set(cfg["modules"])
    for m in cfg["modules"]:
        need = []
        for other in sorted(all_mods):
            if other == m:
                continue
            for sym in sorted(mod_defs[other]):
                if sym in mod_used[m] and sym not in header_imported and sym not in per_module:
                    need.append((other, sym))
        cross[m] = need

    os.makedirs(subdir, exist_ok=True)
    with open(os.path.join(subdir, "__init__.py"), "w", encoding="utf-8", newline=nl) as f:
        f.write(f'"""`{sublib}`：{cfg["module"]} 的内部实现子包，由 facade 分发，勿直接 import。"""\n')

    for m in cfg["modules"]:
        parts = [_module_docstring(m, sorted(mod_defs[m]), module_doc)]
        parts.append("# ── 复制自原模块的顶层 import ──")
        parts.append(header_src)
        if cross[m]:
            parts.append("# ── 跨子模块依赖（自动生成）──")
            parts.append("\n".join(f"from .{other} import {sym}" for other, sym in cross[m]))
        if per_module_src:
            parts.append(per_module_src)
        parts.append("# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──")
        for names, pre, body in mod_nodes[m]:
            if pre.strip():
                parts.append(pre)
            parts.append(body)
        parts.append("")
        parts.append("__all__ = %s" % json.dumps(sorted(mod_defs[m]), ensure_ascii=False))
        content = "\n\n".join(parts) + "\n"
        with open(os.path.join(subdir, m + ".py"), "w", encoding="utf-8", newline=nl) as f:
            f.write(content)
        print(f"[ok] {sublib}/{m}.py  {len(content.splitlines())} lines")

    # ---- 类分块 ----
    chunk_mods = []  # (module_name, class_name, members)
    for cname, cspec in cfg.get("class_split", {}).items():
        cls_node = cls_nodes[cname]
        chunks = build_class_chunks(cls_node, cspec, src_lines)
        for ci, ch in enumerate(chunks):
            cm = cspec["chunks"][ci]["module"]
            used = set(per_module.keys())
            for pre, body in ch["members"]:
                used.update(collect_used_from_str(body))
            need_cross = sorted(sym for sym in sorted(all_regular_defs)
                                if sym in used and sym not in header_imported and sym not in per_module)
            parts = [_module_docstring(cm, [ch["class"]], module_doc)]
            parts.append("# ── 复制自原模块的顶层 import ──")
            parts.append(header_src)
            imp_lines = []
            if ch["base"]:
                prev_mod = cspec["chunks"][ci - 1]["module"]
                imp_lines.append(f"from .{prev_mod} import {ch['base']}")
            if need_cross:
                imp_lines.append("# ── 跨子模块依赖（自动生成）──")
                imp_lines += [f"from .{m} import {sym}" for m in sorted(all_mods) for sym in need_cross if sym in mod_defs[m]]
            if imp_lines:
                parts.append("\n".join(imp_lines))
            if per_module_src:
                parts.append(per_module_src)
            parts.append("# ── 类分块（verbatim，继承链切片）──")
            base_txt = f"({ch['base']})" if ch["base"] else ""
            parts.append(f"class {ch['class']}{base_txt}:")
            if not ch["members"]:
                parts.append("    pass")
            for pre, body in ch["members"]:
                if pre.strip():
                    parts.append(pre)
                parts.append(body)
            parts.append("")
            parts.append("__all__ = ['%s']" % ch["class"])
            content = "\n".join(parts) + "\n"
            with open(os.path.join(subdir, cm + ".py"), "w", encoding="utf-8", newline=nl) as f:
                f.write(content)
            print(f"[ok] {sublib}/{cm}.py  {len(content.splitlines())} lines")
            chunk_mods.append((cm, ch["class"]))

    # ---- facade ----
    facade_names = []
    for m in cfg["modules"]:
        for sym in sorted(mod_defs[m]):
            if sym not in facade_names:
                facade_names.append(sym)
    for cname in class_names:
        if cname not in facade_names:
            facade_names.append(cname)
    for k in sorted(per_module):
        if k not in facade_names:
            facade_names.append(k)

    facade_lines = [
        f'"""{cfg["module"]} 拆分 facade —— 代码已拆入 `{sublib}/`（{", ".join(list(cfg["modules"]) + [c for c in class_names])}），',
        '本模块保持原有 import 路径（含下划线私有符号）与 __all__。"""',
        "# 由 split_module.py 生成，勿手工改动此文件头",
    ]
    for m in cfg["modules"]:
        facade_lines.append(f"from .{sublib}.{m} import *")
    for cm, cname in chunk_mods:
        facade_lines.append(f"from .{sublib}.{cm} import {cname}")
    if per_module:
        facade_lines.append("import logging")
        for k in sorted(per_module):
            facade_lines.append(f"{k} = {per_module[k]}")
    facade_lines.append("")
    facade_lines.append("__all__ = %s" % json.dumps(facade_names, ensure_ascii=False))
    with open(src, "w", encoding="utf-8", newline=nl) as f:
        f.write("\n".join(facade_lines) + "\n")
    print(f"[ok] facade 重写: {cfg['src']}  -> {len(facade_names)} 个导出符号")

    check_path = src + ".names.json"
    with open(check_path, "w", encoding="utf-8") as f:
        json.dump({"module": cfg["module"], "names": facade_names}, f, ensure_ascii=False)


def build_blocks(src_lines, statements, per_module):
    blocks = []
    prev_end = 0
    for names, node, start in statements:
        if names[0] in per_module:
            continue
        pre = pre_comment(src_lines, prev_end, start)
        body = _source_lines(src_lines, node.lineno, node.end_lineno)
        blocks.append((names, pre, body))
        prev_end = max(prev_end, node.end_lineno)
        prev_end = max(prev_end, start + lines_len(node) - 1)
    return blocks


def collect_used_from_str(body):
    try:
        node = ast.parse(textwrap.dedent(body))
    except SyntaxError:
        return set()
    used = set()
    for stmt in node.body:
        used |= collect_used(stmt)
    return used


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

CONFIGS = {
    "graph": {
        "src": r"backend/app/agent/graph.py",
        "module": "app.agent.graph",
        "sublib": "graphmod",
        "per_module_consts": {"logger": "logging.getLogger(__name__)"},
        "modules": {
            "constants": [
                "MAX_STEPS_PROMPT", "DOOM_LOOP_PROMPT", "_FINISH_REASON_MAP",
                "_normalize_finish_reason", "_permission_denied_msg",
                "_nearest_workspace_hint", "_DEDUP_READONLY_TOOLS",
                "_TASK_TOOL_SUBAGENTS", "_TASK_TOOL_SCHEMA", "_is_multi_agent_queue",
            ],
            "state": [
                "AgentState", "_ZERO_USAGE", "_extract_cache_usage",
                "_find_attachment", "_attachment_parts",
            ],
        },
        "class_split": {
            "RAGAgent": {
                "chunks": [
                    {"module": "base", "class": "RAGAgentBase", "base": None,
                     "members": ["__init__", "rebuild_system_prompt", "_activity_text",
                                 "_push_event", "_retrieve", "_rerank",
                                 "_system_prompt_with_kb", "_tool_matches_intent",
                                 "_build_tool_defs", "_pinned_tool_names",
                                 "_bound_plugin_result"],
                     "ranges": [[470, 508]]},
                    {"module": "tools", "class": "RAGAgentTools", "base": "RAGAgentBase",
                     "members": ["_task_tool_placeholder", "_memory_tool_placeholder",
                                 "_tool_task", "_tool_memory", "_execute_tool",
                                 "_execute_tool_streaming"]},
                    {"module": "generate", "class": "RAGAgentGenerate", "base": "RAGAgentTools",
                     "members": ["_generate"]},
                    {"module": "core", "class": "RAGAgent", "base": "RAGAgentGenerate",
                     "members": ["_push_stream_event", "_assemble_response", "_llm_call",
                                 "_build_graph", "refresh_tools", "invoke"]},
                ],
            },
        },
    },
    "chat": {
        "src": r"backend/app/api/chat.py",
        "module": "app.api.chat",
        "sublib": "chatmod",
        "per_module_consts": {"logger": "logging.getLogger(__name__)"},
        "modules": {
            "helpers": [
                "_DEFAULT_USER_ID", "_get_user_id", "MAX_HISTORY_TOKENS",
                "MAX_MESSAGE_LENGTH", "_summarizer", "_summarizer_model",
                "_get_summarizer", "reset_summarizer", "_generate_title",
                "_truncate_history", "_ALLOWED_HISTORY_KEYS", "_sanitize_history",
                "_get_session_service", "_msg_type_to_role", "_validate_chat_message",
            ],
            "persist": [
                "_resolve_multi_agent_parent", "_session_history_for",
                "_build_compressed_history", "_begin_task_session",
                "_persist_multi_agent_parts", "_persist_multi_agent",
                "_existing_pair", "_ensure_child_pair", "_persist_interrupted_partial",
            ],
            "endpoints": [
                "MAX_CONCURRENT_AGENTS", "_agent_semaphore", "_queue_counter",
                "MAX_QUEUE_SIZE", "_get_agent_semaphore", "router",
                "chat_multi_agent", "chat_multi_agent_stream",
            ],
        },
    },
    "file_tools": {
        "src": r"backend/app/tools/file_tools.py",
        "module": "app.tools.file_tools",
        "sublib": "fstools",
        "modules": {
            "common": [
                "_env", "unwrap", "_coerce_int", "_coerce_bool",
                "_IMAGE_EXTS", "_TEXT_EXTS", "_PDF_EXTS", "_AUDIO_EXTS",
                "_VIDEO_EXTS", "_DOC_EXTS", "_MULTIMODAL_EXTS", "_MIME_MAP",
                "DEFAULT_READ_LIMIT", "MAX_LINE_LENGTH", "MAX_LINE_SUFFIX",
                "MAX_BYTES", "MAX_BYTES_LABEL", "SAMPLE_BYTES", "_BINARY_EXTS",
            ],
            "workspace": [
                "_WORKSPACE_FALLBACK", "_matcher_cache", "_gitignore_matcher",
                "_gitignore_checker", "_scan_cache", "_workspace", "_resolve",
                "_ensure_safe", "_is_read_allowed",
            ],
            "reader": [
                "_file_not_found_suggestion", "tool_ls", "_is_binary",
                "tool_read_file", "_list_directory", "_file_not_found_envelope",
            ],
            "writer": [
                "_detect_line_ending", "_normalize_line_endings",
                "_convert_line_ending", "_read_text_raw", "_write_text_raw",
                "tool_write_file", "tool_append_file", "_edit_levenshtein",
                "_edit_line_positions", "_edit_simple_replacer",
                "_edit_line_trimmed_replacer", "_edit_block_anchor_replacer",
                "_edit_whitespace_normalized_replacer",
                "_edit_indentation_flexible_replacer",
                "_edit_escape_normalized_replacer",
                "_edit_trimmed_boundary_replacer", "_edit_context_aware_replacer",
                "_edit_multi_occurrence_replacer", "_EDIT_REPLACERS", "_edit_replace",
                "tool_edit_file", "tool_delete_file", "tool_rename_file",
            ],
            "patch": [
                "_patch_split_sections", "_patch_add_body", "_patch_update_hunks",
                "_patch_apply_hunks", "tool_apply_patch",
            ],
            "search": ["tool_glob", "tool_grep"],
            "lexcmd": [
                "_SHELL_SEP", "_REDIRECT_OPS", "_CMD_CMDSEP_CHARS",
                "_CMD_REDIRECT_CHARS", "_CMD_REDIRECT_OP_TOKENS",
                "_WRITE_REDIRECT_OPS", "_cmd_lex", "_cmd_split_shell_segments",
                "_win_flag_split", "_is_redirect_token", "_first_command",
                "_extract_redirect_targets", "_check_redirect_targets_permission",
            ],
            "execv": [
                "_ALLOWED_COMMANDS",
                "_split_shell_segments", "_BACKTICK_RE", "_backtick_bodies",
                "_check_single_allowed", "_validate_shell_command", "_WIN_CMD_BUILTINS",
                "_WIN_SHIM_EXTS", "_win_which_cache", "_win_cmd_needs_shell",
                "_needs_shell", "_check_command_allowed", "_DANGEROUS_PATTERNS",
                "_check_command_blacklist", "_NET_COMMANDS", "_ssrf_check_command",
            ],
            "exec": [
                "MAX_EXECUTE_OUTPUT_LENGTH", "_kill_process_tree",
                "decode_process_output", "_run_shell", "_CMD_DIALECT_HINT",
                "append_cmd_dialect_hint", "_format_execute_output", "tool_execute",
            ],
        },
    },
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("key", choices=list(CONFIGS))
    args = ap.parse_args()
    split(CONFIGS[args.key])