"""数据飞轮测试: 特征工程 / RatchetDataStore / PatchClassifier(方案 #2)"""
from __future__ import annotations

import pytest

from goal_studio.core.ratchet_data import (
    RatchetDataStore,
    RefineRecord,
    build_feature_vector,
    extract_delta_features,
    extract_graph_features,
    extract_patch_features,
)
from goal_studio.core.training import (
    MIN_SAMPLES,
    SKLEARN_AVAILABLE,
    NotTrainedError,
    PatchClassifier,
)


def _graph_dict(n_l1: int = 3, n_l2: int = 2, edges: int = 2,
                cond_edges: int = 0, slots_per_node: int = 1) -> dict:
    nodes: dict[str, dict] = {}
    for i in range(n_l1):
        nodes[f"l1-{i}"] = {
            "id": f"l1-{i}", "layer": 1, "title": f"任务{i}",
            "status": "ready", "slots": {f"k{j}": j for j in range(slots_per_node)},
        }
    for i in range(n_l2):
        nodes[f"l2-{i}"] = {
            "id": f"l2-{i}", "layer": 2, "title": f"角色{i}",
            "status": "draft", "slots": {},
        }
    edge_list = []
    for i in range(edges):
        is_cond = i < cond_edges
        edge_list.append({
            "id": f"e{i}", "source": f"l1-{i}", "target": f"l1-{i + 1}",
            "edge_type": "condition" if is_cond else "data",
        })
    return {"nodes": nodes, "edges": edge_list, "root_id": "l1-0", "version": 1}


def _record(decision: str, *, before: dict | None = None, after: dict | None = None,
            patch: dict | None = None, old: float = 70.0, new: float = 75.0) -> RefineRecord:
    return RefineRecord(
        session_id="s-test", goal="测试目标",
        graph_before=before or _graph_dict(),
        graph_after=after or _graph_dict(n_l1=4, edges=3),
        patch=patch or {"op": "add_node", "target_node_id": "l1-3",
                        "slots": {}, "new_node": {"layer": 1}},
        old_score=old, new_score=new, decision=decision,
    )


class TestFeatureExtractor:
    def test_graph_features(self) -> None:
        g = _graph_dict(n_l1=3, n_l2=2, edges=2, cond_edges=1, slots_per_node=2)
        f = extract_graph_features(g)
        assert f["node_count"] == 5
        assert f["edge_count"] == 2
        assert f["l1_node_count"] == 3
        assert f["l2_node_count"] == 2
        assert f["l3_node_count"] == 0
        assert f["condition_edge_ratio"] == pytest.approx(0.5)
        assert f["avg_slots_per_node"] == pytest.approx(6 / 5)
        assert f["ready_node_ratio"] == pytest.approx(3 / 5)

    def test_empty_graph_features(self) -> None:
        f = extract_graph_features({"nodes": {}, "edges": []})
        assert f["node_count"] == 0
        assert f["condition_edge_ratio"] == 0.0
        assert f["isolated_node_ratio"] == 0.0

    def test_isolated_node_ratio(self) -> None:
        g = _graph_dict(n_l1=3, edges=2)  # 3 个 L1 连通,2 个 L2 孤立
        f = extract_graph_features(g)
        assert f["isolated_node_ratio"] == pytest.approx(2 / 5)

    def test_patch_features_onehot(self) -> None:
        f = extract_patch_features({"op": "add_node", "new_node": {"layer": 2}})
        assert f["op_add_node"] == 1.0
        assert f["op_update_node"] == 0.0
        assert f["has_new_node"] == 1.0
        assert f["target_layer"] == 2.0

    def test_patch_features_slots_changed(self) -> None:
        f = extract_patch_features(
            {"op": "update_node", "slots": {"title": "x", "desc": "y"}}
        )
        assert f["slots_changed"] == 2.0
        assert f["has_new_node"] == 0.0

    def test_patch_features_condition_change(self) -> None:
        f = extract_patch_features(
            {"op": "add_edge", "slots": {"condition": "分数 >= 60"}}
        )
        assert f["is_condition_change"] == 1.0

    def test_delta_features(self) -> None:
        d = extract_delta_features(_graph_dict(n_l1=3), _graph_dict(n_l1=4))
        assert d["delta_node_count"] == 1.0
        assert d["delta_l1_node_count"] == 1.0
        assert d["delta_l2_node_count"] == 0.0

    def test_full_feature_vector(self) -> None:
        v = build_feature_vector(_record("keep"))
        # 三组特征 + 分数上下文都在
        assert "node_count" in v and "op_add_node" in v and "delta_node_count" in v
        assert v["score_delta"] == pytest.approx(5.0)
        assert all(isinstance(x, float) for x in v.values())


class TestRatchetDataStore:
    def test_save_and_training_data(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = RatchetDataStore(tmp_path / "ratchet.db")
        store.save_refine_record(_record("keep"))
        store.save_refine_record(_record("revert", new=65.0))
        X, y = store.get_training_data()  # noqa: N806 — ML 惯例(X/y)
        assert len(X) == 2 and y == [1, 0]
        assert X[0]["score_delta"] == pytest.approx(5.0)
        assert X[1]["score_delta"] == pytest.approx(-5.0)

    def test_stats(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = RatchetDataStore(tmp_path / "ratchet.db")
        store.save_refine_record(_record("keep"))
        store.save_refine_record(_record("revert"))
        store.save_refine_record(_record("early_stop", new=70.5))
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["keep"] == 1 and stats["revert"] == 1
        assert stats["early_stop"] == 1 and stats["other"] == 0

    def test_label_property(self) -> None:
        assert _record("keep").label == 1
        assert _record("revert").label == 0
        assert _record("early_stop").label == 0

    def test_prune(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        store = RatchetDataStore(tmp_path / "ratchet.db")
        for _ in range(5):
            store.save_refine_record(_record("keep"))
        deleted = store.prune(keep_last=2)
        assert deleted == 3
        assert store.stats()["total"] == 2

    def test_persistence_across_instances(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = tmp_path / "ratchet.db"
        RatchetDataStore(db).save_refine_record(_record("keep"))
        assert RatchetDataStore(db).stats()["total"] == 1


def _synthetic_dataset(n: int = 120) -> tuple[list[dict[str, float]], list[int]]:
    """可分数据集: slots_changed + delta_node_count 与 KEEP 正相关"""
    X: list[dict[str, float]] = []  # noqa: N806 — ML 惯例(X/y)
    y: list[int] = []
    for i in range(n):
        slots_changed = float(i % 4)          # 0-3
        delta_nodes = float(i % 3)            # 0-2
        old_score = 60.0 + (i % 5)
        score = slots_changed * 2 + delta_nodes * 3 - 4
        keep = 1 if score > 1 else 0
        X.append({
            "slots_changed": slots_changed,
            "delta_node_count": delta_nodes,
            "old_score": old_score,
            "node_count": 10.0 + delta_nodes,
        })
        y.append(keep)
    return X, y


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn 未安装")
class TestPatchClassifier:
    def test_train_metrics_and_importance(self) -> None:
        X, y = _synthetic_dataset()  # noqa: N806 — ML 惯例(X/y)
        clf = PatchClassifier()
        metrics = clf.train(X, y)
        assert metrics["accuracy"] >= 0.85
        assert metrics["eval"] == "holdout"
        assert metrics["n_keep"] + metrics["n_revert"] == len(X)
        # 特征重要性可解释: 相关特征排前面
        top = [f["feature"] for f in metrics["feature_importance"][:3]]
        assert "slots_changed" in top or "delta_node_count" in top
        assert clf.is_trained

    def test_predict_aligns_features(self) -> None:
        """预测侧缺失/多余特征键自动对齐(缺失补 0)"""
        X, y = _synthetic_dataset()  # noqa: N806 — ML 惯例(X/y)
        clf = PatchClassifier()
        clf.train(X, y)
        prob, decision = clf.predict({
            "slots_changed": 3.0, "delta_node_count": 2.0,
            "old_score": 62.0, "unknown_extra": 1.0,
        })
        assert 0.0 <= prob <= 1.0
        assert decision == "KEEP" and prob > 0.5
        prob2, decision2 = clf.predict({"slots_changed": 0.0, "delta_node_count": 0.0})
        assert decision2 == "REVERT" and prob2 < 0.5

    def test_save_load_roundtrip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        X, y = _synthetic_dataset()  # noqa: N806 — ML 惯例(X/y)
        clf = PatchClassifier()
        clf.train(X, y)
        path = clf.save(tmp_path / "model.pkl")
        loaded = PatchClassifier.load(path)
        assert loaded.is_trained
        assert loaded.feature_names == clf.feature_names
        p1, _ = clf.predict({"slots_changed": 3.0, "delta_node_count": 2.0})
        p2, _ = loaded.predict({"slots_changed": 3.0, "delta_node_count": 2.0})
        assert p1 == pytest.approx(p2)

    def test_insufficient_samples_raises(self) -> None:
        clf = PatchClassifier()
        with pytest.raises(RuntimeError, match="样本不足"):
            clf.train([{"a": 1.0}] * 5, [1, 0, 1, 0, 1])

    def test_single_class_raises(self) -> None:
        clf = PatchClassifier()
        with pytest.raises(RuntimeError, match="单类别"):
            clf.train([{"a": float(i)} for i in range(40)], [1] * 40)


class TestPatchClassifierFallback:
    def test_predict_untrained_raises(self) -> None:
        with pytest.raises(NotTrainedError):
            PatchClassifier().predict({"a": 1.0})

    def test_load_missing_file_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(NotTrainedError):
            PatchClassifier.load(tmp_path / "nope.pkl")

    @pytest.mark.skipif(SKLEARN_AVAILABLE, reason="sklearn 已安装,走真实训练路径")
    def test_train_without_sklearn_hint(self) -> None:
        with pytest.raises(RuntimeError, match="scikit-learn"):
            PatchClassifier().train(
                [{"a": float(i)} for i in range(MIN_SAMPLES + 10)],
                [i % 2 for i in range(MIN_SAMPLES + 10)],
            )
