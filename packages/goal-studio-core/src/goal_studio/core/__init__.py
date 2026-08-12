"""goal-studio 核心组件: 6 大组件定义"""

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import GoalContract, Patch, Report, Artifact
from goal_studio.core.node_graph import NodeGraph, Node, Edge, NodeLayer
from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.ratchet_gate import RatchetGate, RatchetDecision
from goal_studio.core.knowledge_store import KnowledgeStore, KnowledgeEntry
from goal_studio.core.artifact_store import ArtifactStore
from goal_studio.core.session_manager import SessionManager, Session

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
