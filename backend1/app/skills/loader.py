import logging
import shutil
from pathlib import Path
from typing import List, Optional
import yaml

logger = logging.getLogger(__name__)

# 技能名不允许出现的字符（跨平台安全 + 阻断路径穿越）
_ILLEGAL_NAME_CHARS = set('/\\:*?"<>|')

# [递归技能发现] 嵌套扫描的深度上限（根=0，skills/<分类>/<名字>/SKILL.md 只需 2 层）
_MAX_SCAN_DEPTH = 4
# 递归扫描时跳过的目录（隐藏目录由 startswith('.') 统一处理，这里是可见但无关的）
_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", "env", "site-packages", "dist", "build",
    "target", "out", "coverage", ".egg-info",
}


class Skill:
    """技能数据模型，封装技能的名称、描述、文件路径和启用状态。"""

    def __init__(self, name: str, description: str, path: str, enabled: bool = True,
                 disable_model_invocation: bool = False, managed: bool = False,
                 bundled: bool = False):
        self.name = name
        self.description = description
        self.path = path
        self.enabled = enabled
        # [opencode 对齐] disable-model-invocation: 该技能仅可由用户显式触发，
        # 不暴露为模型的 load_skill_* 工具（避免弱模型误调用，如 to-spec/setup-*）。
        self.disable_model_invocation = disable_model_invocation
        # True = 位于受管库（UI 可编辑/删除）；False = 来自外部源（只读）
        self.managed = managed
        # True = 由内置技能集播种而来（随包发布；UI 标「内置」以便与自建技能区分）
        self.bundled = bundled

    def to_dict(self) -> dict:
        """将技能信息序列化为字典格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "enabled": self.enabled,
            "disable_model_invocation": self.disable_model_invocation,
            "managed": self.managed,
            "bundled": self.bundled,
        }


class SkillLoader:
    """技能加载器：扫描**一个受管库 + 若干外部源**，解析并管理所有技能。

    [受管库 + 多源追加] 第一个目录是受管库（启动时自动创建，UI 新建的技能落这里）；
    `extra_dirs` 是用户追加的外部技能源（如 clone 的技能仓库）。同名技能以**靠前的源
    为准**（受管库优先），保证自建技能不会被外部源覆盖 —— 这是「追加而非替换」的
    关键收益。`skills_dir` 属性保留为受管库，兼容既有调用方。
    """

    def __init__(self, skills_dir: str = "skills", create: bool = True,
                 extra_dirs: List[str] | None = None,
                 bundled_names: set | None = None):
        self.skills_dir = Path(skills_dir)
        self.extra_dirs: List[Path] = [Path(d) for d in (extra_dirs or [])]
        # 内置播种而来的技能名（用于 Skill.bundled 标记，仅影响展示）
        self.bundled_names: set[str] = set(bundled_names or ())
        if create:
            # 只创建受管库；外部源必须是用户已存在的目录，绝不代建
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}
        self._sources: dict[str, str] = {}  # 技能名 -> 来源目录

    # ── 源管理 ──────────────────────────────────────────────────────────

    def all_dirs(self) -> List[Path]:
        """受管库 + 全部外部源（受管库在前，优先级最高）。"""
        return [self.skills_dir, *self.extra_dirs]

    def extra_dir_strings(self) -> List[str]:
        return [str(d) for d in self.extra_dirs]

    def set_dir(self, skills_dir: str) -> None:
        """替换**受管库**目录并重载。UI 走 add_dir（追加外部源），此方法仅供兼容/测试。"""
        new_dir = Path(skills_dir)
        if not new_dir.is_dir():
            raise FileNotFoundError(f"技能目录不存在: {new_dir}")
        self.skills_dir = new_dir
        self.load_all()

    def add_dir(self, skills_dir: str) -> List[Skill]:
        """追加一个外部技能源并重载（重复添加则幂等返回）。"""
        new_dir = Path(skills_dir).expanduser()
        if not new_dir.is_dir():
            raise FileNotFoundError(f"技能目录不存在: {new_dir}")
        resolved = new_dir.resolve()
        if resolved == self.skills_dir.resolve() or any(
            resolved == e.resolve() for e in self.extra_dirs
        ):
            return self.list()
        self.extra_dirs.append(resolved)
        return self.load_all()

    def remove_dir(self, skills_dir: str) -> List[Skill]:
        """移除一个外部技能源并重载（受管库不可移除）。"""
        target = Path(skills_dir).expanduser()
        try:
            resolved = target.resolve()
        except OSError:  # pragma: no cover
            resolved = target
        self.extra_dirs = [
            d for d in self.extra_dirs
            if str(d) != str(target) and _safe_resolve(d) != resolved
        ]
        return self.load_all()

    # ── 扫描 ────────────────────────────────────────────────────────────

    def load_all(self) -> List[Skill]:
        """扫描全部技能源加载技能；同名以靠前的源为准。"""
        self._skills.clear()
        self._sources.clear()
        for root in self.all_dirs():
            if not root.is_dir():
                if root == self.skills_dir:
                    logger.warning("受管技能库不存在，跳过加载: %s", root)
                else:
                    logger.warning("外部技能源不存在，跳过加载: %s", root)
                continue
            for skill in self._scan_dir(root, managed=(root == self.skills_dir)):
                if skill.name in self._skills:
                    logger.warning(
                        "技能名冲突：%s 已被 %s 提供，跳过 %s（受管库/靠前的源优先）",
                        skill.name, self._sources[skill.name], skill.path,
                    )
                    continue
                self._skills[skill.name] = skill
                self._sources[skill.name] = str(root)
        return self.list()

    def _scan_dir(self, root: Path, managed: bool) -> List[Skill]:
        """扫描单个目录：根层平铺 *.md + 任意深度的 <dir>/SKILL.md。

        [递归技能发现] 技能仓库常按分类嵌套（``<root>/engineering/tdd/SKILL.md``），
        只扫一层会整类漏掉。规则刻意区分两层，避免把分类说明文档误判为技能：
          - 深度 0（根目录自身）：``*.md`` 平铺技能（既有 opencode 约定，行为不变）
          - 深度 ≥1：仅「**含 SKILL.md 的目录**」算技能 → ``engineering/README.md``
            这类分类说明不会被当成名为 ``README`` 的技能（6 个分类 README 会互相覆盖）。
        """
        found: List[Skill] = []
        for f in sorted(root.glob("*.md")):
            skill = self._load_skill_file(f, managed=managed)
            if skill:
                found.append(skill)
        found.extend(self._scan_nested(root, managed=managed, depth=1))
        return found

    def _scan_nested(self, root: Path, managed: bool, depth: int) -> List[Skill]:
        """递归发现 ``<dir>/SKILL.md``（含子目录的技能，其配套文件也随目录保留）。"""
        found: List[Skill] = []
        if depth > _MAX_SCAN_DEPTH:
            return found
        try:
            entries = sorted(root.iterdir())
        except OSError:  # pragma: no cover - 权限/并发删除
            return found
        for subdir in entries:
            try:
                if not subdir.is_dir() or subdir.is_symlink():
                    continue
            except OSError:  # pragma: no cover
                continue
            if subdir.name.startswith(".") or subdir.name in _SKIP_DIRS:
                continue
            skill_file = subdir / "SKILL.md"
            try:
                has_skill = skill_file.is_file()
            except OSError:  # pragma: no cover
                has_skill = False
            if has_skill:
                # [opencode 对齐] 子目录技能的身份是**目录名**（foo/SKILL.md → "foo"），
                # frontmatter 的 name 可省略。此前回退到 path.stem 会让所有无 name 的
                # 子目录技能都叫 "SKILL"，在 _skills 字典里互相覆盖 → 静默丢技能。
                skill = self._load_skill_file(skill_file, fallback_name=subdir.name,
                                              managed=managed)
                if skill:
                    found.append(skill)
            found.extend(self._scan_nested(subdir, managed=managed, depth=depth + 1))
        return found

    def _load_skill_file(self, path: Path, fallback_name: str = "",
                         managed: bool = False) -> Optional[Skill]:
        """解析单个技能Markdown文件，提取YAML frontmatter中的元信息。

        fallback_name: frontmatter 缺少 name 时使用的名称（子目录技能传目录名）；
        为空则回退到文件名词干（顶层平铺的 .md 技能）。
        """
        try:
            content = path.read_text(encoding="utf-8")
            meta, _body = _split_frontmatter(content)

            name = str(meta.get("name") or fallback_name or path.stem).strip()
            description = meta.get("description", "") or content[:200].strip()

            return Skill(
                name=name,
                description=description,
                path=str(path),
                enabled=meta.get("enabled", True),
                disable_model_invocation=bool(
                    meta.get("disable-model-invocation",
                             meta.get("disable_model_invocation", False))
                ),
                managed=managed,
                bundled=name in self.bundled_names,
            )
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", path.name, e)
            return None

    # ── 受管库 CRUD（供 UI「新建/编辑/删除技能」）─────────────────────────

    def create_skill(self, name: str, description: str = "", content: str = "",
                     *, disable_model_invocation: bool = False,
                     enabled: bool = True) -> Skill:
        """在受管库新建技能（写 `<managed>/<name>/SKILL.md`）并重载。"""
        n = _validate_name(name)
        existing = self.get(n)
        if existing is not None and existing.managed:
            raise ValueError(f"技能已存在: {n}")
        if existing is not None:
            # 外部源已有同名技能：允许在受管库建同名技能来「遮蔽」它
            #（受管库优先是本设计的关键收益，否则用户无法覆盖 clone 来的技能）
            logger.info("受管技能 %s 将遮蔽外部技能（来源 %s）", n, self.source_of(n))
        d = self.skills_dir / n
        d.mkdir(parents=True, exist_ok=True)
        meta: dict = {"name": n, "description": description, "enabled": bool(enabled)}
        if disable_model_invocation:
            meta["disable-model-invocation"] = True
        body = content.strip() or f"# {n}\n"
        f = d / "SKILL.md"
        f.write_text(
            "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n" + body + "\n",
            encoding="utf-8",
        )
        self.load_all()
        created = self.get(n)
        if created is None:  # pragma: no cover - 写完却扫不到属于异常
            raise RuntimeError(f"技能创建后未能加载: {n}")
        return created

    def update_skill(self, name: str, *, description: Optional[str] = None,
                     content: Optional[str] = None,
                     disable_model_invocation: Optional[bool] = None,
                     enabled: Optional[bool] = None) -> Skill:
        """编辑受管库中的技能；未传的字段保持原样（保留其它 frontmatter 键）。"""
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"技能不存在: {name}")
        if not skill.managed:
            raise PermissionError(f"外部技能源的技能不可编辑: {name}（{skill.path}）")
        path = Path(skill.path)
        raw = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(raw)

        if description is not None:
            meta["description"] = description
        if disable_model_invocation is not None:
            if disable_model_invocation:
                meta["disable-model-invocation"] = True
            else:
                meta.pop("disable-model-invocation", None)
                meta.pop("disable_model_invocation", None)
        if enabled is not None:
            meta["enabled"] = bool(enabled)
        if content is not None:
            body = content.strip() or body

        path.write_text(
            "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n" + body.strip() + "\n",
            encoding="utf-8",
        )
        self.load_all()
        updated = self.get(skill.name)
        if updated is None:  # pragma: no cover
            raise RuntimeError(f"技能更新后未能加载: {name}")
        return updated

    def delete_skill(self, name: str) -> bool:
        """删除**受管库**中的技能文件/目录。外部源的技能一律拒绝。"""
        skill = self._skills.get(name)
        if skill is None:
            return False
        path = Path(skill.path).resolve()
        managed_root = self.skills_dir.resolve()
        if managed_root not in path.parents:
            logger.warning("拒绝删除非受管技能: %s (%s)", name, path)
            return False
        if path.parent == managed_root:      # 顶层平铺的 <managed>/foo.md
            path.unlink(missing_ok=True)
        else:                                # <managed>/foo/SKILL.md（含嵌套 <managed>/a/b/）
            target_dir = path.parent
            shutil.rmtree(target_dir, ignore_errors=True)
            # [递归发现] 嵌套技能删除后把沿途空目录一并清掉，避免受管库里留垃圾骨架
            parent = target_dir.parent
            while managed_root in parent.parents:
                try:
                    next(parent.iterdir())
                    break            # 还有别的内容，停止上溯
                except StopIteration:
                    parent.rmdir()
                    parent = parent.parent
                except OSError:      # pragma: no cover
                    break
        self.load_all()
        return True

    # ── 查询 ────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Skill]:
        """根据技能名称获取技能实例。"""
        return self._skills.get(name)

    def source_of(self, name: str) -> str:
        """技能所属源目录（用于前端标注来源 / 排查冲突）。"""
        return self._sources.get(name, "")

    def list(self) -> List[Skill]:
        """返回所有已加载的技能列表。"""
        return list(self._skills.values())

    def toggle(self, name: str, enabled: bool) -> bool:
        """启用或禁用指定技能，并将状态写回Markdown文件。"""
        skill = self._skills.get(name)
        if not skill:
            return False
        skill.enabled = enabled
        try:
            self._save_skill_file(skill)
        except Exception:
            return False
        return True

    def _save_skill_file(self, skill: Skill) -> None:
        """将技能的元信息（含启用状态）写回其Markdown文件的YAML frontmatter。"""
        path = Path(skill.path)
        content = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(content)

        meta["name"] = skill.name
        meta["description"] = skill.description
        meta["enabled"] = skill.enabled

        new_content = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + body
        path.write_text(new_content, encoding="utf-8")

    def get_enabled_skills(self) -> List[Skill]:
        """获取所有已启用的技能列表。"""
        return [s for s in self._skills.values() if s.enabled]

    def get_skill_content(self, name: str) -> Optional[str]:
        """读取指定技能文件的完整文本内容（含 frontmatter）。"""
        skill = self._skills.get(name)
        if not skill:
            return None
        try:
            return Path(skill.path).read_text(encoding="utf-8")
        except Exception:
            return None

    def get_skill_body(self, name: str) -> Optional[str]:
        """读取技能**正文**（剥掉 YAML frontmatter）。

        编辑表单只改正文，元信息（name/description/enabled）由独立的表单字段提交。
        若把含 frontmatter 的全文回填进正文框，保存时 `update_skill` 会再套一层
        frontmatter → 产出 `---\n...\n---\n---\nname: x\n...` 的嵌套损坏文件。
        """
        raw = self.get_skill_content(name)
        if raw is None:
            return None
        _meta, body = _split_frontmatter(raw)
        return body


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """把 Markdown 拆成 (YAML frontmatter 字典, 正文)。

    只在**首行就是 `---` 分隔线**时才认为存在 frontmatter —— 仅靠
    `content.split("---", 2)` 的片段数判断是不够的：正文里出现 `---`
    （markdown 分隔线、YAML 风格的 `key: value` 段落）时会把正文误当 frontmatter
    解析，进而在 `_save_skill_file` / `update_skill` 回写时损坏用户正文。

    任何异常形态（无 frontmatter、YAML 非法、顶层不是映射）都退回 (空字典, 全文)。
    """
    parts = content.split("---", 2)
    if len(parts) < 3 or parts[0].strip() != "":
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content
    if not isinstance(meta, dict):
        return {}, content
    return meta, parts[2].lstrip("\n")


def _safe_resolve(p: Path) -> str:
    try:
        return str(p.resolve())
    except OSError:  # pragma: no cover
        return str(p)


def _validate_name(name: str) -> str:
    """校验技能名：非空、无路径分隔符/非法字符、非 . / ..（防路径穿越）。"""
    n = str(name or "").strip()
    if not n:
        raise ValueError("技能名不能为空")
    if n in (".", ".."):
        raise ValueError(f"非法技能名: {n}")
    bad = _ILLEGAL_NAME_CHARS & set(n)
    if bad:
        raise ValueError(f"技能名含非法字符 {''.join(sorted(bad))}: {n}")
    if len(n) > 64:
        raise ValueError("技能名过长（上限 64 字符）")
    return n
