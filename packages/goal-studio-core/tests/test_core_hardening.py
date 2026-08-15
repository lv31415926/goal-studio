"""硬核化修复回归测试

覆盖:
  C7  apply_ratchet_decision 用 RuntimeError 替代 assert
  F3  SessionPhase 死枚举清理 + 历史数据兼容
  C3  NodeGraph._edge_ids O(1) 去重索引
  A1/P1-3  SessionManager 锁持有者计数 + 空闲锁清扫
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer
from goal_studio.core.ratchet_gate import RatchetDecision
from goal_studio.core.session_manager import Session, SessionManager, SessionPhase

# ==== C7 ====

def test_apply_ratchet_decision_none_graph_raises() -> None:
    """C7: 图为空时显式 RuntimeError(替代 python -O 下失效的 assert)"""
    s = Session()
    with pytest.raises(RuntimeError, match="会话图为空"):
        s.apply_ratchet_decision(RatchetDecision.REVERT, NodeGraph(), 5.0)


def test_apply_ratchet_decision_keep_ok() -> None:
    """C7: 正常 KEEP 路径不受影响"""
    g = NodeGraph()
    g.add_node(Node(id="n1", layer=NodeLayer.L1_PLANNING, title="t"))
    s = Session()
    s.ratchet.judge(0.0, 10.0, g)
    out = s.apply_ratchet_decision(RatchetDecision.KEEP, g, 10.0)
    assert out is g
    assert s.graph is g


# ==== F3 ====

def test_session_phase_removed_value_falls_back() -> None:
    """F3: 已移除的 paused 值反序列化降级为 INIT"""
    assert SessionPhase("paused") is SessionPhase.INIT
    assert "paused" not in [p.value for p in SessionPhase]


def test_session_from_dict_legacy_phase(tmp_path: Path) -> None:
    """F3: 历史会话文件 phase=paused 可正常加载"""
    mgr = SessionManager(tmp_path / "s")
    s = mgr.create(name="legacy")
    path = tmp_path / "s" / f"{s.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["phase"] = "paused"
    path.write_text(json.dumps(data), encoding="utf-8")
    mgr.cleanup_session(s.id)
    loaded = mgr.get(s.id)
    assert loaded.phase is SessionPhase.INIT


# ==== C3 ====

def _mk_graph() -> NodeGraph:
    g = NodeGraph()
    for nid in ("a", "b", "c"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid))
    return g


def test_add_edge_duplicate_id_dedup() -> None:
    """C3: 同 id 边跳过(undo→redo→undo 不重复)"""
    g = _mk_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g.add_edge(Edge(id="e1", source="a", target="c"))
    assert len(g.edges) == 1
    assert g.edges[0].target == "b"


def test_remove_edge_frees_id() -> None:
    """C3: remove_edge 后 id 可复用"""
    g = _mk_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    assert g.remove_edge("e1")
    g.add_edge(Edge(id="e1", source="a", target="c"))
    assert g.edges[0].target == "c"


def test_remove_node_frees_edge_ids() -> None:
    """C3: remove_node 级联移除边后,边 id 同步释放"""
    g = _mk_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g.remove_node("b", cascade=False)
    g.add_node(Node(id="d", layer=NodeLayer.L1_PLANNING, title="d"))
    g.add_edge(Edge(id="e1", source="a", target="d"))
    assert g.edges[0].target == "d"


def test_edge_ids_roundtrip_and_self_heal() -> None:
    """C3: from_dict 重建索引;索引被破坏时 validate 自愈"""
    g = _mk_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g2 = NodeGraph.from_dict(g.to_dict())
    assert g2._edge_ids == {"e1"}
    g2._edge_ids.clear()  # 人为破坏
    g2.validate()
    assert g2._edge_ids == {"e1"}


# ==== A1 / P1-3 ====

def test_session_lock_holder_counting_and_reentrancy(tmp_path: Path) -> None:
    """A1: 嵌套获取计数正确,完全释放后计数清零"""
    mgr = SessionManager(tmp_path / "s")
    s = mgr.create(name="t")
    with mgr.session_lock(s.id):
        assert mgr._lock_holders[s.id] == 1
        with mgr.session_lock(s.id):
            assert mgr._lock_holders[s.id] == 2
        assert mgr._lock_holders[s.id] == 1
    assert s.id not in mgr._lock_holders


def test_idle_locks_swept_over_max(tmp_path: Path, monkeypatch) -> None:
    """A1: 锁字典超上限时清扫无持有者且不在缓存的空闲锁"""
    monkeypatch.setattr(SessionManager, "_LOCKS_MAX", 2)
    mgr = SessionManager(tmp_path / "s")
    ids = []
    for i in range(5):
        s = mgr.create(name=f"s{i}")
        ids.append(s.id)
        mgr.cleanup_session(s.id)  # 清缓存 → 锁成为空闲候选
    assert len(mgr._session_locks) <= 2
    # 最新会话仍在缓存中,其锁不被清扫
    mgr.get(ids[-1])
    with mgr.session_lock(ids[-1]):
        assert ids[-1] in mgr._session_locks


def test_distinct_sessions_distinct_locks(tmp_path: Path) -> None:
    """P1-3: 不同 session 锁互不阻塞(错误实现会死锁,靠测试超时暴露)"""
    mgr = SessionManager(tmp_path / "s")
    a, b = mgr.create(name="a"), mgr.create(name="b")
    with mgr.session_lock(a.id), mgr.session_lock(b.id):
        pass
