"""goal-studio: 可视化 Loop 创作系统"""

__version__ = "0.1.0"

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer

__all__ = [
    "BaseAdapter",
    "GoalContract",
    "Patch",
    "Report",
    "Artifact",
    "NodeGraph",
    "Node",
    "Edge",
    "NodeLayer",
]
