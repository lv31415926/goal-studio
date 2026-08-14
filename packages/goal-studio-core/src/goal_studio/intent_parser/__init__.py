"""goal-studio IntentParser 五层架构

Layer 1: ModalityIngestion  - 多模态信号采集
Layer 2: SignalBuffer        - 时间窗口对齐 (200ms, 2s 滑动)
Layer 3: IncrementalLLMParser - LLM 增量解析输出 JSON
Layer 4: NodeReferenceResolver - 三信号融合算法消解节点引用
Layer 5: ActionExecutor      - DAG mutation + undo/redo
"""

from goal_studio.intent_parser.action_executor import ActionExecutor
from goal_studio.intent_parser.llm_parser import IncrementalLLMParser, ParsedIntent
from goal_studio.intent_parser.modality_ingestion import ModalityIngestion, ModalitySignal
from goal_studio.intent_parser.node_resolver import NodeReferenceResolver, ResolveResult
from goal_studio.intent_parser.pipeline import IntentPipeline
from goal_studio.intent_parser.router import ModalityRoute, ModalityRouter
from goal_studio.intent_parser.signal_buffer import SignalBuffer

__all__ = [
    "ModalityIngestion", "ModalitySignal",
    "SignalBuffer",
    "IncrementalLLMParser", "ParsedIntent",
    "NodeReferenceResolver", "ResolveResult",
    "ActionExecutor",
    "ModalityRouter", "ModalityRoute",
    "IntentPipeline",
]
