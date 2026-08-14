"""goal-studio 内置适配器: skill / code / doc / workflow"""

from goal_studio.adapters.code_adapter import CodeAdapter
from goal_studio.adapters.doc_adapter import DocAdapter
from goal_studio.adapters.skill_adapter import SkillAdapter
from goal_studio.adapters.workflow_adapter import WorkflowAdapter

__all__ = ["SkillAdapter", "CodeAdapter", "DocAdapter", "WorkflowAdapter"]
