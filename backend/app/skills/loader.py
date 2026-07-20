import logging
from pathlib import Path
from typing import List, Optional
import yaml

logger = logging.getLogger(__name__)


class Skill:
    """技能数据模型，封装技能的名称、描述、文件路径和启用状态。"""

    def __init__(self, name: str, description: str, path: str, enabled: bool = True):
        self.name = name
        self.description = description
        self.path = path
        self.enabled = enabled

    def to_dict(self) -> dict:
        """将技能信息序列化为字典格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "enabled": self.enabled,
        }


class SkillLoader:
    """技能加载器，负责从Markdown文件扫描、解析和管理所有技能。"""

    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}

    def load_all(self) -> List[Skill]:
        """扫描技能目录，加载所有.md格式的技能文件（含子目录中的SKILL.md）。"""
        self._skills.clear()

        for f in self.skills_dir.glob("*.md"):
            skill = self._load_skill_file(f)
            if skill:
                self._skills[skill.name] = skill

        for subdir in self.skills_dir.iterdir():
            if not subdir.is_dir():
                continue
            skill_file = subdir / "SKILL.md"
            if not skill_file.exists():
                continue
            skill = self._load_skill_file(skill_file)
            if skill:
                self._skills[skill.name] = skill

        return self.list()

    def _load_skill_file(self, path: Path) -> Optional[Skill]:
        """解析单个技能Markdown文件，提取YAML frontmatter中的元信息。"""
        try:
            content = path.read_text(encoding="utf-8")
            parts = content.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
            else:
                meta = {}

            name = meta.get("name", path.stem)
            description = meta.get("description", "") or content[:200].strip()

            return Skill(
                name=name,
                description=description,
                path=str(path),
                enabled=meta.get("enabled", True),
            )
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", path.name, e)
            return None

    def get(self, name: str) -> Optional[Skill]:
        """根据技能名称获取技能实例。"""
        return self._skills.get(name)

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
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].lstrip("\n")
            meta = yaml.safe_load(parts[1]) or {}
        else:
            body = content
            meta = {}

        meta["name"] = skill.name
        meta["description"] = skill.description
        meta["enabled"] = skill.enabled

        new_content = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n" + body
        path.write_text(new_content, encoding="utf-8")

    def get_enabled_skills(self) -> List[Skill]:
        """获取所有已启用的技能列表。"""
        return [s for s in self._skills.values() if s.enabled]

    def get_skill_content(self, name: str) -> Optional[str]:
        """读取指定技能文件的完整文本内容。"""
        skill = self._skills.get(name)
        if not skill:
            return None
        try:
            return Path(skill.path).read_text(encoding="utf-8")
        except Exception:
            return None
