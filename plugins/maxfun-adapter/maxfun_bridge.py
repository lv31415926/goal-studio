"""MaxfunBridge: goal-studio ↔ maxfun 双向桥接器

功能:
  1. install_to_maxfun — 将 goal-studio 产物安装到 maxfun skill 目录
  2. import_from_maxfun — 将 maxfun 已有 skill 导入为 goal-studio DAG
  3. convert_skill_md — goal-studio SKILL.md → maxfun skill 格式
  4. detect_maxfun_dir — 自动探测 maxfun skill 目录

设计原则:
  - 核心引擎零耦合,不依赖 goal_studio 内部实现
  - 通过 Artifact (goal-studio 产物) 和 SKILL.md (maxfun 格式) 作为桥梁
  - 支持离线操作,不要求 maxfun 运行时存在
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MaxfunSkillMeta:
    """maxfun skill 元数据(从 SKILL.md frontmatter 提取)"""

    name: str = ""
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    entry: str = "main.py"
    schedule: str = ""

    def to_frontmatter(self) -> str:
        """生成 YAML frontmatter"""
        lines = ["---"]
        lines.append(f"name: {self.name}")
        lines.append(f"description: {self.description}")
        if self.triggers:
            lines.append("triggers:")
            for t in self.triggers:
                lines.append(f"  - {t}")
        lines.append(f"version: {self.version}")
        lines.append(f"author: {self.author}")
        if self.tags:
            lines.append("tags:")
            for tag in self.tags:
                lines.append(f"  - {tag}")
        lines.append(f"entry: {self.entry}")
        if self.schedule:
            lines.append(f"schedule: {self.schedule}")
        lines.append("---")
        return "\n".join(lines)

    @classmethod
    def from_frontmatter(cls, content: str) -> MaxfunSkillMeta:
        """从 SKILL.md 内容提取 frontmatter"""
        # 归一化 CRLF → LF,兼容 Windows 换行
        content = content.replace("\r\n", "\n")
        meta = cls()
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return meta
        fm = match.group(1)
        # 简单 YAML 解析(避免依赖 pyyaml)
        # 状态机: 记录当前正在解析的列表字段(triggers/tags)
        current_list: str | None = None
        for line in fm.split("\n"):
            line = line.strip()
            if line.startswith("- ") and current_list is not None:
                # 列表项归入当前字段
                val = line[2:].strip()
                if current_list == "triggers":
                    meta.triggers.append(val)
                elif current_list == "tags":
                    meta.tags.append(val)
                continue
            if line.startswith("name:"):
                meta.name = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("description:"):
                meta.description = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("version:"):
                meta.version = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("author:"):
                meta.author = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("entry:"):
                meta.entry = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("schedule:"):
                meta.schedule = line.split(":", 1)[1].strip()
                current_list = None
            elif line.startswith("triggers:"):
                current_list = "triggers"
            elif line.startswith("tags:"):
                current_list = "tags"
            elif line:
                # 非空非列表行 → 退出列表上下文
                current_list = None
        return meta


def detect_maxfun_dir() -> Path | None:
    """自动探测 maxfun skill 目录

    查找顺序:
      1. MAXFUN_SKILL_DIR 环境变量
      2. ~/.maxfun/skills/
      3. ~/.trae-cn/skills/
      4. 当前目录下的 skills/
    """
    # 1. 环境变量
    env_dir = os.environ.get("MAXFUN_SKILL_DIR")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)

    # 2-3. 常见路径
    home = Path.home()
    candidates = [
        home / ".maxfun" / "skills",
        home / ".trae-cn" / "skills",
        Path.cwd() / "skills",
    ]
    for c in candidates:
        if c.is_dir():
            return c

    return None


def install_to_maxfun(
    artifact_files: dict[str, str],
    skill_name: str,
    maxfun_dir: str | Path | None = None,
    skill_meta: MaxfunSkillMeta | None = None,
) -> Path:
    """将 goal-studio 产物安装到 maxfun skill 目录

    Args:
        artifact_files: 产物文件映射 {filename: content}
        skill_name: skill 名称
        maxfun_dir: maxfun skill 根目录(默认自动探测)
        skill_meta: skill 元数据(默认从 SKILL.md 提取)

    Returns:
        安装路径
    """
    if maxfun_dir is None:
        maxfun_dir = detect_maxfun_dir()
        if maxfun_dir is None:
            raise FileNotFoundError(
                "未找到 maxfun skill 目录。请设置 MAXFUN_SKILL_DIR 环境变量"
                "或创建 ~/.maxfun/skills/"
            )
    maxfun_dir = Path(maxfun_dir)

    # PR-G/G-5: 校验 skill_name,防止路径遍历
    import re
    if not skill_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", skill_name):
        raise ValueError(f"非法 skill_name: {skill_name}")

    # 创建 skill 目录
    skill_path = maxfun_dir / skill_name
    skill_path.mkdir(parents=True, exist_ok=True)

    # 提取或使用传入的元数据
    skill_md_content = artifact_files.get("SKILL.md", "")
    # 归一化 CRLF → LF,兼容 Windows 换行
    skill_md_content = skill_md_content.replace("\r\n", "\n")
    if skill_meta is None:
        skill_meta = MaxfunSkillMeta.from_frontmatter(skill_md_content)
        skill_meta.name = skill_name

    # 转换 SKILL.md 为 maxfun 格式(加 frontmatter)
    maxfun_skill_md = skill_meta.to_frontmatter() + "\n\n"
    # 去掉原始 frontmatter(如果有)
    body = re.sub(r"^---\n.*?\n---\n*", "", skill_md_content, flags=re.DOTALL)
    maxfun_skill_md += body

    # 写入文件
    (skill_path / "SKILL.md").write_text(maxfun_skill_md, encoding="utf-8")

    # 写入其他产物文件
    skill_path_resolved = skill_path.resolve()
    for fname, content in artifact_files.items():
        if fname == "SKILL.md":
            continue
        file_path = (skill_path / fname).resolve()
        try:
            file_path.relative_to(skill_path_resolved)
        except ValueError:
            raise ValueError(f"路径遍历: {fname} 不在 {skill_path} 内")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # 生成 maxfun manifest.json
    manifest = {
        "name": skill_meta.name,
        "version": skill_meta.version,
        "description": skill_meta.description,
        "entry": skill_meta.entry,
        "triggers": skill_meta.triggers,
        "tags": skill_meta.tags,
        "source": "goal-studio",
        "files": list(artifact_files.keys()),
    }
    (skill_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return skill_path


def import_from_maxfun(
    skill_dir: str | Path,
) -> dict[str, Any]:
    """将 maxfun skill 导入为 goal-studio DAG 结构

    Args:
        skill_dir: maxfun skill 目录路径

    Returns:
        导入结果 {skill_meta, files, dag_hint}
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"Skill 目录不存在: {skill_dir}")

    # 读取 SKILL.md
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_content = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else ""
    # 归一化 CRLF → LF,兼容 Windows 换行
    skill_md_content = skill_md_content.replace("\r\n", "\n")
    meta = MaxfunSkillMeta.from_frontmatter(skill_md_content)

    # 读取 manifest.json(如果存在)
    manifest_path = skill_dir / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 收集所有文件
    files: dict[str, str] = {}
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.name not in ("manifest.json",):
            rel_path = str(f.relative_to(skill_dir))
            try:
                files[rel_path] = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[rel_path] = f"[binary file: {f.stat().st_size} bytes]"

    # 从 SKILL.md body 生成 DAG 提示(简单提取标题作为 L1 节点)
    dag_hint: list[dict[str, str]] = []
    body = re.sub(r"^---\n.*?\n---\n*", "", skill_md_content, flags=re.DOTALL)
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            dag_hint.append({
                "id": f"l1-{len(dag_hint) + 1}",
                "title": line.lstrip("# ").strip(),
                "layer": "L1",
                "type": "task",
            })

    return {
        "skill_meta": meta.__dict__,
        "manifest": manifest,
        "files": files,
        "dag_hint": dag_hint,
        "source_dir": str(skill_dir),
    }


def list_maxfun_skills(
    maxfun_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """列出 maxfun 目录中的所有 skill

    Returns:
        skill 信息列表 [{name, path, meta, file_count}]
    """
    if maxfun_dir is None:
        maxfun_dir = detect_maxfun_dir()
        if maxfun_dir is None:
            return []
    maxfun_dir = Path(maxfun_dir)

    skills: list[dict[str, Any]] = []
    for d in sorted(maxfun_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        meta = MaxfunSkillMeta.from_frontmatter(skill_md.read_text(encoding="utf-8"))
        file_count = sum(1 for _ in d.rglob("*") if _.is_file())
        skills.append({
            "name": d.name,
            "path": str(d),
            "description": meta.description,
            "version": meta.version,
            "triggers": meta.triggers,
            "file_count": file_count,
        })
    return skills
