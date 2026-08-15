"""RatchetDataStore: 棘轮数据飞轮 — refine 判定数据沉淀 + 特征工程

把每次 /refine 的 (graph_before, graph_after, patch, old/new_score, decision)
沉淀为结构化样本,供 PatchClassifier(梯度提升树,见 training.py)离线训练,
预测补丁 KEEP/REVERT 概率 → 前置识别高概率回滚的精化。

特征体系(三层):
  1. 图结构特征: 节点/边规模、三层分布、条件边占比、孤立节点率、槽位填充度
  2. 补丁特征: 操作类型 one-hot、槽位改动数、目标层级、是否触碰条件边
  3. 差分特征: 补丁前后的图规模/条件边占比变化 + 分数上下文

零第三方依赖: sqlite3 + json(stdlib),与 core 包依赖策略一致。
所有特征计算基于序列化 graph dict(to_dict 产物),不依赖运行时对象。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class RefineRecord:
    """一次 refine 判定的完整样本"""

    session_id: str
    goal: str
    graph_before: dict[str, Any]
    graph_after: dict[str, Any]
    patch: dict[str, Any]
    old_score: float
    new_score: float
    decision: str
    """keep / revert / early_stop / arbitrate"""

    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def label(self) -> int:
        """KEEP=1(修改被保留),其余(回滚/早停)=0"""
        return 1 if self.decision == "keep" else 0


def _layer_count(graph: dict[str, Any], layer: int) -> float:
    return float(
        sum(1 for n in graph.get("nodes", {}).values() if n.get("layer") == layer)
    )


def _condition_edge_count(graph: dict[str, Any]) -> float:
    return float(
        sum(1 for e in graph.get("edges", []) if e.get("edge_type") == "condition")
    )


def extract_graph_features(graph: dict[str, Any]) -> dict[str, float]:
    """图结构特征(基于 to_dict 序列化格式)"""
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])
    n = len(nodes)
    m = len(edges)
    cond = _condition_edge_count(graph)
    connected: set[str] = set()
    for e in edges:
        connected.add(str(e.get("source")))
        connected.add(str(e.get("target")))
    isolated = sum(1 for nid in nodes if nid not in connected)
    slots_total = sum(len(n.get("slots", {})) for n in nodes.values())
    ready = sum(
        1
        for n in nodes.values()
        if n.get("status") in ("ready", "done")
    )
    return {
        "node_count": float(n),
        "edge_count": float(m),
        "l1_node_count": _layer_count(graph, 1),
        "l2_node_count": _layer_count(graph, 2),
        "l3_node_count": _layer_count(graph, 3),
        "condition_edge_ratio": cond / m if m else 0.0,
        "avg_out_degree": m / n if n else 0.0,
        "isolated_node_ratio": isolated / n if n else 0.0,
        "avg_slots_per_node": slots_total / n if n else 0.0,
        "ready_node_ratio": ready / n if n else 0.0,
    }


_PATCH_TYPE_KEYS = (
    "add_node",
    "update_node",
    "remove_node",
    "add_edge",
    "remove_edge",
    "reorder",
)


def extract_patch_features(patch: dict[str, Any]) -> dict[str, float]:
    """补丁特征: 操作类型 one-hot + 改动规模 + 目标层级 + 条件触碰"""
    op = str(patch.get("op", ""))
    slots = patch.get("slots") or {}
    new_node = patch.get("new_node") or {}
    features: dict[str, float] = {
        f"op_{k}": 1.0 if op == k else 0.0 for k in _PATCH_TYPE_KEYS
    }
    features["slots_changed"] = (
        float(len(slots)) if op == "update_node" else 0.0
    )
    features["has_new_node"] = 1.0 if new_node else 0.0
    layer = new_node.get("layer") if isinstance(new_node, dict) else None
    features["target_layer"] = float(layer) if isinstance(layer, int) else 0.0
    # 条件触碰: 条件边增删,或 slots/new_node 携带 condition 语义
    touches = op in ("add_edge", "remove_edge") and bool(
        slots.get("condition") or slots.get("edge_type") == "condition"
    )
    touches = touches or bool(slots.get("condition"))
    features["is_condition_change"] = 1.0 if touches else 0.0
    return features


def extract_delta_features(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, float]:
    """差分特征: 补丁前后图结构变化"""
    b = extract_graph_features(before)
    a = extract_graph_features(after)
    return {
        "delta_node_count": a["node_count"] - b["node_count"],
        "delta_edge_count": a["edge_count"] - b["edge_count"],
        "delta_condition_edge_ratio": (
            a["condition_edge_ratio"] - b["condition_edge_ratio"]
        ),
        "delta_l1_node_count": a["l1_node_count"] - b["l1_node_count"],
        "delta_l2_node_count": a["l2_node_count"] - b["l2_node_count"],
        "delta_l3_node_count": a["l3_node_count"] - b["l3_node_count"],
    }


def build_feature_vector(record: RefineRecord) -> dict[str, float]:
    """组装完整特征向量: 图(前) + 补丁 + 差分 + 分数上下文"""
    features: dict[str, float] = {}
    features.update(extract_graph_features(record.graph_before))
    features.update(extract_patch_features(record.patch))
    features.update(
        extract_delta_features(record.graph_before, record.graph_after)
    )
    features["old_score"] = float(record.old_score)
    features["new_score"] = float(record.new_score)
    features["score_delta"] = float(record.new_score - record.old_score)
    return features


class RatchetDataStore:
    """SQLite 样本库 — 线程安全(write 锁),支持训练数据导出与统计

    Args:
        db_path: SQLite 文件路径(父目录需已存在,由调用方保证)
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS refine_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    decision TEXT NOT NULL,
                    old_score REAL NOT NULL,
                    new_score REAL NOT NULL,
                    features TEXT NOT NULL,
                    graph_before TEXT NOT NULL,
                    graph_after TEXT NOT NULL,
                    patch TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_refine_session"
                " ON refine_records(session_id)"
            )
            self._conn.commit()

    def save_refine_record(self, record: RefineRecord) -> int:
        """沉淀一条样本(含特征提取),返回行 ID"""
        features = build_feature_vector(record)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO refine_records
                    (session_id, goal, decision, old_score, new_score,
                     features, graph_before, graph_after, patch, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.goal,
                    record.decision,
                    record.old_score,
                    record.new_score,
                    json.dumps(features, ensure_ascii=False),
                    json.dumps(record.graph_before, ensure_ascii=False),
                    json.dumps(record.graph_after, ensure_ascii=False),
                    json.dumps(record.patch, ensure_ascii=False),
                    record.timestamp,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_training_data(self) -> tuple[list[dict[str, float]], list[int]]:
        """导出训练数据: (特征向量列表, 标签列表)"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT features, decision FROM refine_records"
            ).fetchall()
        X: list[dict[str, float]] = []  # noqa: N806 — ML 惯例(X/y)
        y: list[int] = []
        for features_json, decision in rows:
            feats = json.loads(str(features_json))
            if not isinstance(feats, dict):
                continue
            X.append({k: float(v) for k, v in feats.items()})
            y.append(1 if str(decision) == "keep" else 0)
        return X, y

    def stats(self) -> dict[str, Any]:
        """样本统计: 总量 + 各判定分布"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT decision, COUNT(*) FROM refine_records GROUP BY decision"
            ).fetchall()
        by_decision = {str(d): int(c) for d, c in rows}
        total = sum(by_decision.values())
        return {
            "total": total,
            "keep": by_decision.get("keep", 0),
            "revert": by_decision.get("revert", 0),
            "early_stop": by_decision.get("early_stop", 0),
            "other": total
            - by_decision.get("keep", 0)
            - by_decision.get("revert", 0)
            - by_decision.get("early_stop", 0),
            "db_path": self.db_path,
        }

    def prune(self, keep_last: int = 5000) -> int:
        """按创建时间保留最近 N 条,返回删除条数(防库无限膨胀)"""
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM refine_records WHERE id NOT IN (
                    SELECT id FROM refine_records
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (keep_last,),
            )
            self._conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
