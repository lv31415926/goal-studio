"""IntentPipeline: 五层流水线整合

Layer 1 → Layer 2 → Layer 3 → Layer 4 → Layer 5
采集    → 对齐    → LLM解析  → 节点消解  → 动作执行
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from goal_studio.core.node_graph import NodeGraph
from goal_studio.intent_parser.action_executor import ActionExecutor, ExecutionResult
from goal_studio.intent_parser.llm_parser import IncrementalLLMParser, ParsedIntent
from goal_studio.intent_parser.modality_ingestion import ModalityIngestion
from goal_studio.intent_parser.node_resolver import NodeReferenceResolver, ResolveResult
from goal_studio.intent_parser.router import ModalityRoute, ModalityRouter
from goal_studio.intent_parser.signal_buffer import SignalBuffer
from goal_studio.llm.base import BaseLLMProvider


@dataclass
class PipelineResult:
    """流水线处理结果"""
    route: ModalityRoute = ModalityRoute.VOICE_PRIMARY
    intent: ParsedIntent | None = None
    resolve: ResolveResult | None = None
    execution: ExecutionResult | None = None
    final_graph: NodeGraph | None = None
    error: str = ""
    # 边界场景: ASR延迟 — 说话开始时刻的画布快照(锁定上下文,避免用户已切换导致消歧错乱)
    canvas_snapshot: NodeGraph | None = None


class IntentPipeline:
    """五层意图解析流水线"""

    def __init__(
        self,
        graph: NodeGraph,
        llm: BaseLLMProvider | None = None,
        dialog_history: list[dict[str, str]] | None = None,
    ) -> None:
        self.graph = graph
        self.ingestion = ModalityIngestion()
        self.buffer = SignalBuffer()
        self.parser = IncrementalLLMParser(llm)
        self.executor = ActionExecutor()
        self.router = ModalityRouter()
        self.dialog_history = dialog_history or []
        # R1-17: 串行化 process/undo/redo/update_graph(同步方法,用 threading.Lock)
        self._lock = threading.Lock()

    def update_graph(self, graph: NodeGraph) -> None:
        """更新当前图"""
        # R1-17 + R2-15: 加锁 + 重置 parser 上下文(避免省略恢复指向旧图节点)
        with self._lock:
            self.graph = graph
            self.parser.reset_context()

    def ingest_voice(self, transcript: str, confidence: float = 0.9) -> None:
        """接收语音转录"""
        sig = self.ingestion.ingest_voice(transcript, confidence)
        self.buffer.push(sig)

    def ingest_text(self, text: str) -> None:
        """接收文本输入"""
        sig = self.ingestion.ingest_text(text)
        self.buffer.push(sig)

    def ingest_mouse_select(self, node_id: str) -> None:
        """接收鼠标选中"""
        sig = self.ingestion.ingest_mouse("select", node_id=node_id)
        self.buffer.push(sig)

    def ingest_mouse_hover(self, node_id: str | None) -> None:
        """接收鼠标悬停"""
        if node_id:
            sig = self.ingestion.ingest_mouse("hover", node_id=node_id)
        else:
            sig = self.ingestion.ingest_mouse("unhover")
        self.buffer.push(sig)

    def process(
        self,
        text: str | None = None,
        is_mobile: bool = False,
        force: bool = False,
    ) -> PipelineResult:
        """处理当前输入

        Args:
            text: 文本输入(若 None 则从 buffer 取最新 voice/text)
            is_mobile: 是否移动端
            force: 边界场景 — 确认后强制执行(跳过删除级联确认门控)

        Returns:
            PipelineResult
        """
        # R1-17: 加锁串行化,避免并发 process/undo/redo 互相踩踏
        with self._lock:
            return self._process_impl(text, is_mobile, force)

    def _process_impl(
        self,
        text: str | None,
        is_mobile: bool,
        force: bool,
    ) -> PipelineResult:
        result = PipelineResult()

        # 边界场景: ASR延迟 — 锁定说话开始时刻的画布状态快照
        # ASR 慢时用户可能已切换选中/悬停,用快照保证消歧用说话时刻的画布状态
        # PR-M: 深拷贝,避免后续 process 修改 self.graph 污染快照语义
        result.canvas_snapshot = NodeGraph.from_dict(self.graph.to_dict())

        # Layer 1+2: 对齐信号
        aligned = self.buffer.align()
        if text:
            self.ingest_text(text)
            aligned = self.buffer.align()

        input_text = text or aligned.primary_text
        if not input_text:
            result.error = "无输入文本"
            return result

        # 模态路由
        result.route = self.router.route(input_text, aligned, is_mobile)

        # R2-14: Layer 3 (LLM 解析) try/except
        try:
            canvas_summary = self._build_canvas_summary()
            result.intent = self.parser.parse(
                input_text,
                canvas_summary=canvas_summary,
                dialog_history=self.dialog_history,
            )
        except Exception as e:  # noqa: BLE001
            result.error = f"Layer3 解析异常: {e}"
            return result

        if not result.intent.is_valid:
            # 歧义或无效
            return result

        # R2-14: Layer 4 (节点引用消解) try/except
        if result.intent.target_ref:
            try:
                resolver = NodeReferenceResolver(
                    graph=self.graph,
                    recent_stack=self.buffer.recent_nodes,
                    current_selection=aligned.selection,
                    current_hover=aligned.hover,
                )
                result.resolve = resolver.resolve(
                    ref_type=result.intent.target_ref.get("type", "pronoun"),
                    ref_value=result.intent.target_ref.get("value", ""),
                    asr_confidence=(
                        aligned.voice.metadata.get("asr_confidence", 0.9)
                        if aligned.voice
                        else 1.0
                    ),
                )
            except Exception as e:  # noqa: BLE001
                result.error = f"Layer4 消解异常: {e}"
                return result

            if not result.resolve.ok:
                return result

            # 回填消解结果到 parser 上下文(供后续省略恢复)
            if result.resolve.node_id:
                self.parser.backfill_resolved_node(result.resolve.node_id)

        # R2-14: Layer 5 (动作执行) try/except + 事务语义
        # 边界场景: 多模态冲突 — 传递当前 hover 供 executor 检测冲突
        node_id = result.resolve.node_id if result.resolve else None
        try:
            result.execution = self.executor.execute(
                intent=result.intent,
                graph=self.graph,
                resolved_node_id=node_id,
                force=force,
                conflict_hover_id=aligned.hover,
            )
        except Exception as e:  # noqa: BLE001
            result.error = f"Layer5 执行异常: {e}"
            return result

        # R2-14: 事务语义 — 仅当 execution.ok 且 new_graph 有效时,最后才替换 self.graph
        if result.execution.ok and result.execution.new_graph:
            result.final_graph = result.execution.new_graph
            self.graph = result.execution.new_graph

        return result

    def _build_canvas_summary(self) -> str:
        """构建画布摘要(供 LLM 消歧)"""
        lines = [f"当前 DAG: {len(self.graph.nodes)} 节点, {len(self.graph.edges)} 边"]
        for nid, node in list(self.graph.nodes.items())[:10]:  # 最多10个
            lines.append(f"  [{nid}] {node.title} (L{node.layer.value}) - {node.description[:40]}")
        return "\n".join(lines)

    def undo(self) -> ExecutionResult | None:
        """撤销"""
        # R1-17 + R2-15: 加锁 + 重置 parser 上下文
        with self._lock:
            result = self.executor.undo(self.graph)
            if result and result.new_graph:
                self.graph = result.new_graph
                self.parser.reset_context()
            return result

    def redo(self) -> ExecutionResult | None:
        """重做"""
        # R1-17 + R2-15: 加锁 + 重置 parser 上下文
        with self._lock:
            result = self.executor.redo(self.graph)
            if result and result.new_graph:
                self.graph = result.new_graph
                self.parser.reset_context()
            return result
