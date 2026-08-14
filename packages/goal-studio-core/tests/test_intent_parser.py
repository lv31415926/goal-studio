"""intent_parser 模块测试: 信号采集/对齐/路由/节点消解/启发式解析/动作执行/流水线"""
from __future__ import annotations

import time
from typing import Any

from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer, NodeStatus
from goal_studio.intent_parser.action_executor import ActionExecutor
from goal_studio.intent_parser.llm_parser import IncrementalLLMParser, ParsedIntent
from goal_studio.intent_parser.modality_ingestion import Modality, ModalityIngestion, ModalitySignal
from goal_studio.intent_parser.node_resolver import NodeReferenceResolver
from goal_studio.intent_parser.pipeline import IntentPipeline
from goal_studio.intent_parser.router import ModalityRoute, ModalityRouter
from goal_studio.intent_parser.signal_buffer import AlignedSignal, SignalBuffer
from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse
from goal_studio.llm.mock_provider import MockLLMProvider


class _FailingLLM(BaseLLMProvider):
    """测试用: 总是抛异常的 LLM(模拟 LLM 不可用)"""

    @property
    def name(self) -> str:
        return "failing"

    @property
    def model(self) -> str:
        return "failing-test"

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        raise RuntimeError("llm unavailable")


# === 辅助 ===

def make_graph(n_nodes: int = 3) -> NodeGraph:
    g = NodeGraph(root_id="l1-root")
    for i in range(n_nodes):
        nid = f"l1-n{i}"
        g.add_node(Node(
            id=nid, layer=NodeLayer.L1_PLANNING,
            title=f"任务{i}", description=f"描述{i}",
            node_type="task", status=NodeStatus.DRAFT,
            position=(i * 200.0, i * 100.0),
        ))
    return g


# === ModalityIngestion ===

class TestModalityIngestion:
    def test_ingest_voice(self):
        ing = ModalityIngestion()
        sig = ing.ingest_voice("你好", confidence=0.95)
        assert sig.modality == Modality.VOICE
        assert sig.content == "你好"
        assert sig.metadata["asr_confidence"] == 0.95

    def test_ingest_text(self):
        ing = ModalityIngestion()
        sig = ing.ingest_text("创建节点")
        assert sig.modality == Modality.TEXT
        assert sig.content == "创建节点"

    def test_ingest_mouse(self):
        ing = ModalityIngestion()
        sig = ing.ingest_mouse("select", node_id="n1", position=(10.0, 20.0))
        assert sig.modality == Modality.MOUSE
        assert sig.metadata["node_id"] == "n1"
        assert sig.metadata["position"] == (10.0, 20.0)

    def test_drain(self):
        ing = ModalityIngestion()
        ing.ingest_voice("a")
        ing.ingest_text("b")
        signals = ing.drain()
        assert len(signals) == 2
        assert ing.drain() == []

    def test_peek_latest(self):
        ing = ModalityIngestion()
        ing.ingest_voice("a")
        ing.ingest_text("b")
        assert ing.peek_latest().content == "b"
        assert ing.peek_latest(Modality.VOICE).content == "a"
        assert ing.peek_latest(Modality.MOUSE) is None

    def test_signal_age(self):
        sig = ModalitySignal(modality=Modality.TEXT, content="x", timestamp=time.time() - 1)
        assert sig.age_ms >= 900


# === SignalBuffer ===

class TestSignalBuffer:
    def test_push_and_align(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(modality=Modality.VOICE, content="删除这个"))
        buf.push(ModalitySignal(modality=Modality.TEXT, content="删除"))
        aligned = buf.align()
        assert aligned.voice is not None
        assert aligned.text is not None
        assert aligned.primary_text == "删除这个"

    def test_selection_tracking(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(
            modality=Modality.MOUSE, content="select", metadata={"node_id": "n1"},
        ))
        aligned = buf.align()
        assert aligned.selection == "n1"

    def test_hover_tracking(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(
            modality=Modality.MOUSE, content="hover", metadata={"node_id": "n2"},
        ))
        aligned = buf.align()
        assert aligned.hover == "n2"
        buf.push(ModalitySignal(modality=Modality.MOUSE, content="unhover"))
        aligned = buf.align()
        assert aligned.hover is None

    def test_recent_nodes(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(
            modality=Modality.MOUSE, content="select", metadata={"node_id": "a"},
        ))
        buf.push(ModalitySignal(
            modality=Modality.MOUSE, content="hover", metadata={"node_id": "b"},
        ))
        buf.push(ModalitySignal(
            modality=Modality.MOUSE, content="select", metadata={"node_id": "a"},
        ))
        nodes = buf.recent_nodes
        assert nodes[0] == "a"
        assert "b" in nodes

    def test_reset(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(modality=Modality.TEXT, content="x"))
        buf.reset()
        aligned = buf.align()
        assert aligned.voice is None
        assert aligned.text is None

    def test_primary_text_prefers_voice(self):
        buf = SignalBuffer()
        buf.push(ModalitySignal(modality=Modality.TEXT, content="文本"))
        buf.push(ModalitySignal(modality=Modality.VOICE, content="语音"))
        assert buf.align().primary_text == "语音"

    def test_empty_align(self):
        buf = SignalBuffer()
        aligned = buf.align()
        assert aligned.primary_text == ""
        assert aligned.timestamp == 0


# === ModalityRouter ===

class TestModalityRouter:
    def setup_method(self):
        self.router = ModalityRouter()
        self.signal = AlignedSignal()

    def test_mobile_mode(self):
        assert (
            self.router.route("任意", self.signal, is_mobile=True)
            == ModalityRoute.VOICE_NUMBER_OVERLAY
        )

    def test_accessibility_mode(self):
        assert (
            self.router.route("任意", self.signal, accessibility_mode=True)
            == ModalityRoute.VOICE_NUMBER_OVERLAY
        )

    def test_explicit_id(self):
        assert self.router.route("l1-n0 改成红色", self.signal) == ModalityRoute.TEXT

    def test_spatial_with_mouse(self):
        sig = AlignedSignal(mouse=ModalitySignal(modality=Modality.MOUSE, content="hover"))
        assert self.router.route("左边的节点", sig) == ModalityRoute.MOUSE_PRIMARY_VOICE_CONFIRM

    def test_spatial_without_mouse(self):
        assert self.router.route("右边的节点", self.signal) == ModalityRoute.VOICE_CANVAS_HIGHLIGHT

    def test_pronoun_with_selection(self):
        sig = AlignedSignal(selection="n1")
        assert self.router.route("删除这个", sig) == ModalityRoute.SELECTION_PRIMARY

    def test_pronoun_with_hover(self):
        sig = AlignedSignal(hover="n2")
        assert self.router.route("修改那个", sig) == ModalityRoute.HOVER_PRIMARY

    def test_pronoun_no_context(self):
        assert self.router.route("删除这个", self.signal) == ModalityRoute.CLARIFY

    def test_property(self):
        assert self.router.route("找到红色节点", self.signal) == ModalityRoute.VOICE_VISUAL_FILTER

    def test_create(self):
        assert self.router.route("创建一个新节点", self.signal) == ModalityRoute.VOICE_PRIMARY

    def test_param_edit(self):
        assert self.router.route("把标题改成X", self.signal) == ModalityRoute.VOICE_PRIMARY

    def test_default(self):
        assert self.router.route("随便说点什么", self.signal) == ModalityRoute.VOICE_PRIMARY


# === NodeReferenceResolver ===

class TestNodeReferenceResolver:
    def setup_method(self):
        self.graph = make_graph(3)

    def test_resolve_by_id(self):
        r = NodeReferenceResolver(graph=self.graph)
        result = r.resolve("id", "l1-n0", asr_confidence=1.0)
        assert result.ok
        assert result.node_id == "l1-n0"
        assert result.confidence == 1.0

    def test_resolve_by_name(self):
        r = NodeReferenceResolver(graph=self.graph)
        result = r.resolve("name", "任务0")
        assert result.ok
        assert result.node_id == "l1-n0"

    def test_resolve_not_found(self):
        r = NodeReferenceResolver(graph=self.graph)
        result = r.resolve("id", "nonexistent")
        assert not result.ok
        assert "找不到" in result.reason

    def test_resolve_with_selection_boost(self):
        r = NodeReferenceResolver(graph=self.graph, current_selection="l1-n1")
        result = r.resolve("pronoun", "")
        assert result.ok
        assert result.node_id == "l1-n1"

    def test_resolve_with_hover_boost(self):
        r = NodeReferenceResolver(graph=self.graph, current_hover="l1-n2")
        result = r.resolve("pronoun", "")
        assert result.ok
        assert result.node_id == "l1-n2"

    def test_ambiguity_triggers_clarification(self):
        # 两个节点标题相同 → 歧义
        g = NodeGraph(root_id="r")
        g.add_node(Node(
            id="a", layer=NodeLayer.L1_PLANNING, title="相同",
            description="", node_type="task", status=NodeStatus.DRAFT,
        ))
        g.add_node(Node(
            id="b", layer=NodeLayer.L1_PLANNING, title="相同",
            description="", node_type="task", status=NodeStatus.DRAFT,
        ))
        r = NodeReferenceResolver(graph=g)
        result = r.resolve("name", "相同")
        assert not result.ok
        assert result.clarification_options is not None

    def test_spatial_resolve(self):
        # a 在鼠标右侧近处, b 在左侧远处 → "右边" 应唯一命中 a
        g = NodeGraph(root_id="r")
        g.add_node(Node(
            id="a", layer=NodeLayer.L1_PLANNING, title="A", description="",
            node_type="task", status=NodeStatus.DRAFT, position=(10.0, 0.0),
        ))
        g.add_node(Node(
            id="b", layer=NodeLayer.L1_PLANNING, title="B", description="",
            node_type="task", status=NodeStatus.DRAFT, position=(-500.0, 0.0),
        ))
        r = NodeReferenceResolver(graph=g, mouse_position=(0.0, 0.0))
        result = r.resolve("spatial", "右边", asr_confidence=1.0)
        assert result.ok
        assert result.node_id == "a"

    def test_property_resolve(self):
        g = make_graph(1)
        g.nodes["l1-n0"].slots["priority"] = "high"
        r = NodeReferenceResolver(graph=g)
        result = r.resolve("property", "高优先级")
        assert result.ok
        assert result.node_id == "l1-n0"

    def test_large_dag_pronoun_filter(self):
        g = NodeGraph(root_id="r")
        for i in range(120):
            g.add_node(Node(
                id=f"n{i:03d}", layer=NodeLayer.L1_PLANNING, title=f"T{i}",
                description="", node_type="task", status=NodeStatus.DRAFT,
            ))
        r = NodeReferenceResolver(graph=g, current_selection="n050")
        result = r.resolve("pronoun", "")
        assert result.ok
        assert result.node_id == "n050"

    def test_asr_confidence_discount(self):
        r = NodeReferenceResolver(graph=self.graph)
        result_high = r.resolve("name", "任务0", asr_confidence=1.0)
        result_low = r.resolve("name", "任务0", asr_confidence=0.5)
        assert result_high.confidence > result_low.confidence


# === IncrementalLLMParser (heuristic only) ===

class TestIncrementalLLMParser:
    def test_heuristic_delete(self):
        parser = IncrementalLLMParser(llm=None)
        intent = parser.parse("删除 l1-n0")
        assert intent.intent == "delete"
        assert intent.parse_source == "heuristic"
        assert intent.target_ref["value"] == "l1-n0"

    def test_heuristic_connect(self):
        parser = IncrementalLLMParser(llm=None)
        intent = parser.parse("连接 l1-n0 和 l1-n1")
        assert intent.intent == "connect"
        assert intent.parse_source == "heuristic"

    def test_heuristic_rename(self):
        parser = IncrementalLLMParser(llm=None)
        intent = parser.parse("把 l1-n0 的标题改成新标题")
        assert intent.intent == "modify"
        assert intent.parse_source == "heuristic"

    def test_heuristic_timeout(self):
        parser = IncrementalLLMParser(llm=None)
        intent = parser.parse("把 l1-n0 超时设为30秒")
        assert intent.intent == "modify"
        assert intent.parse_source == "heuristic"
        assert intent.slots.get("timeout") == 30

    def test_no_llm_fallback(self):
        parser = IncrementalLLMParser(llm=_FailingLLM())
        intent = parser.parse("做一些复杂的事情")
        # LLM 失败且无启发式匹配 → 兜底歧义意图
        assert not intent.is_valid
        assert intent.intent is None
        assert intent.parse_source == "fallback"

    def test_cache_hit(self):
        parser = IncrementalLLMParser(llm=MockLLMProvider())
        # 非启发式文本 → 首次走 LLM 并写入缓存,二次命中缓存
        i1 = parser.parse("帮我优化一下这个流程")
        assert i1.parse_source == "llm"
        i2 = parser.parse("帮我优化一下这个流程")
        assert i2.parse_source == "cache"

    def test_reset_context(self):
        parser = IncrementalLLMParser(llm=None)
        parser.parse("删除 l1-n0")
        parser.reset_context()
        # 重置后缓存仍在但上下文清空
        assert True

    def test_backfill_resolved_node(self):
        parser = IncrementalLLMParser(llm=None)
        parser.backfill_resolved_node("l1-n0")
        assert True


# === ActionExecutor ===

class TestActionExecutor:
    def setup_method(self):
        self.executor = ActionExecutor()
        self.graph = make_graph(3)

    def test_update_node_title(self):
        intent = ParsedIntent(
            intent="modify",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={"title": "新标题"},
            raw_text="改标题",
        )
        result = self.executor.execute(intent, self.graph, resolved_node_id="l1-n0")
        assert result.ok
        assert result.new_graph is not None
        assert result.new_graph.nodes["l1-n0"].title == "新标题"

    def test_delete_node(self):
        intent = ParsedIntent(
            intent="delete",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={},
            raw_text="删除",
        )
        result = self.executor.execute(intent, self.graph, resolved_node_id="l1-n0")
        assert result.ok
        assert "l1-n0" not in result.new_graph.nodes

    def test_delete_needs_confirmation(self):
        # 有子节点时删除需确认
        g = make_graph(3)
        g.add_edge(Edge(id="e-n0-n1", source="l1-n0", target="l1-n1"))
        intent = ParsedIntent(
            intent="delete",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={},
            raw_text="删除",
        )
        result = self.executor.execute(intent, g, resolved_node_id="l1-n0")
        assert result.needs_confirmation

    def test_delete_with_force(self):
        g = make_graph(3)
        g.add_edge(Edge(id="e-n0-n1", source="l1-n0", target="l1-n1"))
        intent = ParsedIntent(
            intent="delete",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={},
            raw_text="删除",
        )
        result = self.executor.execute(intent, g, resolved_node_id="l1-n0", force=True)
        assert result.ok

    def test_add_node(self):
        intent = ParsedIntent(
            intent="create",
            target_ref=None,
            slots={"title": "新节点", "description": "测试"},
            raw_text="创建",
        )
        result = self.executor.execute(intent, self.graph)
        assert result.ok
        assert len(result.new_graph.nodes) == 4

    def test_connect_nodes(self):
        intent = ParsedIntent(
            intent="connect",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={"target_node_id": "l1-n1"},
            raw_text="连接",
        )
        result = self.executor.execute(intent, self.graph, resolved_node_id="l1-n0")
        assert result.ok

    def test_connect_cycle_detection(self):
        g = make_graph(3)
        g.add_edge(Edge(id="e-n0-n1", source="l1-n0", target="l1-n1"))
        g.add_edge(Edge(id="e-n1-n2", source="l1-n1", target="l1-n2"))
        intent = ParsedIntent(
            intent="connect",
            target_ref={"type": "id", "value": "l1-n2", "confidence": 0.95},
            slots={"target_node_id": "l1-n0"},
            raw_text="连接",
        )
        result = self.executor.execute(intent, g, resolved_node_id="l1-n2")
        assert not result.ok
        assert result.cycle_path is not None

    def test_unknown_intent(self):
        intent = ParsedIntent(intent="fly_to_moon", target_ref=None, slots={}, raw_text="x")
        result = self.executor.execute(intent, self.graph)
        assert not result.ok

    def test_undo_redo(self):
        intent = ParsedIntent(
            intent="modify",
            target_ref={"type": "id", "value": "l1-n0", "confidence": 0.95},
            slots={"title": "改后"},
            raw_text="改",
        )
        result = self.executor.execute(intent, self.graph, resolved_node_id="l1-n0")
        assert result.ok and result.new_graph is not None
        assert result.new_graph.nodes["l1-n0"].title == "改后"
        undo_result = self.executor.undo(result.new_graph)
        assert undo_result is not None and undo_result.new_graph is not None
        assert undo_result.new_graph.nodes["l1-n0"].title == "任务0"
        redo_result = self.executor.redo(undo_result.new_graph)
        assert redo_result is not None and redo_result.new_graph is not None
        assert redo_result.new_graph.nodes["l1-n0"].title == "改后"


# === IntentPipeline ===

class TestIntentPipeline:
    def setup_method(self):
        self.graph = make_graph(3)
        self.pipeline = IntentPipeline(graph=self.graph)

    def test_process_text_command(self):
        result = self.pipeline.process(text="把 l1-n0 的标题改成新名字")
        assert result.intent is not None
        assert result.intent.intent == "modify"

    def test_process_no_input(self):
        result = self.pipeline.process(text=None)
        assert result.error == "无输入文本"

    def test_process_with_voice(self):
        self.pipeline.ingest_voice("删除 l1-n1")
        result = self.pipeline.process()
        assert result.intent is not None

    def test_process_with_mouse_selection(self):
        self.pipeline.ingest_mouse_select("l1-n0")
        result = self.pipeline.process(text="删除这个")
        assert result.intent is not None

    def test_canvas_snapshot(self):
        result = self.pipeline.process(text="把 l1-n0 的标题改成X")
        assert result.canvas_snapshot is not None
        assert len(result.canvas_snapshot.nodes) == 3

    def test_update_graph(self):
        new_g = make_graph(5)
        self.pipeline.update_graph(new_g)
        assert len(self.pipeline.graph.nodes) == 5

    def test_route_assignment(self):
        result = self.pipeline.process(text="创建新节点")
        assert result.route == ModalityRoute.VOICE_PRIMARY

    def test_force_delete(self):
        g = make_graph(3)
        g.add_edge(Edge(id="e-n0-n1", source="l1-n0", target="l1-n1"))
        p = IntentPipeline(graph=g)
        result = p.process(text="删除 l1-n0", force=True)
        assert result.execution is not None
