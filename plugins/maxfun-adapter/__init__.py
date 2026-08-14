"""maxfun-adapter: goal-studio ↔ maxfun 双向桥接器

提供:
  - install_to_maxfun: goal-studio 产物 → maxfun skill 目录
  - import_from_maxfun: maxfun skill → goal-studio DAG 结构
  - list_maxfun_skills: 列出 maxfun 目录中的 skill
  - detect_maxfun_dir: 自动探测 maxfun skill 目录
"""
from maxfun_bridge import (  # noqa: F401
    MaxfunSkillMeta,
    detect_maxfun_dir,
    install_to_maxfun,
    import_from_maxfun,
    list_maxfun_skills,
)

__version__ = "0.1.0"
