"""goal-studio 核心组件: 6 大组件定义"""

from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.artifact_store import ArtifactStore
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.knowledge_store import KnowledgeEntry, KnowledgeStore
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer
from goal_studio.core.ratchet_gate import RatchetDecision, RatchetGate
from goal_studio.core.session_manager import Session, SessionManager

__all__ = [
    "BaseAdapter",
    "GoalContract", "Patch", "Report", "Artifact",
    "NodeGraph", "Node", "Edge", "NodeLayer",
    "AdapterRegistry",
    "RatchetGate", "RatchetDecision",
    "KnowledgeStore", "KnowledgeEntry",
    "ArtifactStore",
    "SessionManager", "Session",
]
