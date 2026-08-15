"""PatchClassifier: 梯度提升树补丁成败预测 — 方案 #2 训练路径

数据流: RatchetDataStore 沉淀 refine 判定样本 → build_feature_vector
特征(图结构/补丁/差分/分数) → 梯度提升树二分类(KEEP=1 / REVERT=0)
→ predict 输出 KEEP 概率,供 /refine 前置参考(咨询式,不门控)。

训练步骤(标准路径):
  1. store.get_training_data() 取 (X, y)
  2. 样本量 < MIN_SAMPLES 或单类别 → 拒绝训练(给出明确指引)
  3. 特征对齐(全量键取并排序,缺失补 0)
  4. 分层 train_test_split(样本充足时),否则全量拟合并标注 in-sample
  5. GradientBoostingClassifier 拟合,输出 accuracy/precision/recall/f1
     + feature_importance Top-N(可解释性: 哪些图特征预示回滚)
  6. save(path) 持久化,预测时 load

sklearn 为可选依赖(core 保持零第三方依赖): 缺失时 train() 抛出
带安装指引的 RuntimeError,不影响主流程。
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from sklearn.ensemble import GradientBoostingClassifier  # type: ignore[import-untyped]
    from sklearn.metrics import (  # type: ignore[import-untyped]
        accuracy_score,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import train_test_split  # type: ignore[import-untyped]

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover — 环境相关
    SKLEARN_AVAILABLE = False

MIN_SAMPLES = 30
"""启动训练的最小样本量(低于此值梯度提升树方差过大,宁可不给预测)"""


class NotTrainedError(Exception):
    """模型未训练/未加载即调用 predict"""


def align_features(
    rows: list[dict[str, float]], feature_names: list[str]
) -> list[list[float]]:
    """按固定特征序对齐(缺失键补 0.0),输出模型输入矩阵"""
    return [[float(row.get(k, 0.0)) for k in feature_names] for row in rows]


@dataclass
class PatchClassifier:
    """梯度提升树补丁分类器(可 pickle 持久化)"""

    model: Any | None = None
    """sklearn GradientBoostingClassifier(未训练时为 None)"""

    feature_names: list[str] = field(default_factory=list)
    """训练时的特征序(预测侧必须对齐)"""

    trained_at: str = ""
    train_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trained(self) -> bool:
        return self.model is not None and bool(self.feature_names)

    def train(
        self,
        X: list[dict[str, float]],  # noqa: N803 — ML 惯例(X/y)
        y: list[int],
    ) -> dict[str, Any]:
        """训练梯度提升树,返回评估指标

        Raises:
            RuntimeError: sklearn 未安装 / 样本不足 / 单类别
        """
        if not SKLEARN_AVAILABLE:
            raise RuntimeError(
                "训练需要 scikit-learn: pip install 'goal-studio[flywheel]'"
                " 或 pip install scikit-learn"
            )
        if len(X) != len(y):
            raise RuntimeError(f"X/y 长度不一致: {len(X)} != {len(y)}")
        if len(X) < MIN_SAMPLES:
            raise RuntimeError(
                f"样本不足: {len(X)} < {MIN_SAMPLES},继续沉淀 refine 记录后再训练"
            )
        n_keep = sum(y)
        if n_keep == 0 or n_keep == len(y):
            raise RuntimeError(
                f"单类别样本(keep={n_keep}/{len(y)}),无法训练二分类器"
            )

        # 特征对齐: 全量键并集排序,保证训练/预测一致
        names = sorted({k for row in X for k in row})
        matrix = align_features(X, names)
        labels = [int(v) for v in y]

        # 样本充足时留出 holdout,否则全量拟合(in-sample 指标仅作参考)
        eval_mode = "holdout"
        if len(matrix) >= MIN_SAMPLES * 2:
            X_tr, X_te, y_tr, y_te = train_test_split(  # noqa: N806 — ML 惯例
                matrix,
                labels,
                test_size=0.2,
                random_state=42,
                stratify=labels,
            )
        else:
            X_tr = X_te = matrix  # noqa: N806 — ML 惯例
            y_tr = y_te = labels
            eval_mode = "in-sample"

        model = GradientBoostingClassifier(
            n_estimators=120,
            learning_rate=0.08,
            # 小样本表格数据: 浅树 + 叶子最小样本约束,抑制过拟合
            max_depth=3,
            min_samples_leaf=2,
            random_state=42,
        )
        model.fit(X_tr, y_tr)
        preds = model.predict(X_te)

        importance = sorted(
            zip(names, (float(v) for v in model.feature_importances_),
                strict=True),
            key=lambda kv: -kv[1],
        )
        self.model = model
        self.feature_names = names
        self.trained_at = datetime.now(UTC).isoformat()
        self.train_metrics = {
            "n_samples": len(X),
            "n_features": len(names),
            "n_keep": n_keep,
            "n_revert": len(X) - n_keep,
            "eval": eval_mode,
            "accuracy": float(accuracy_score(y_te, preds)),
            "precision": float(precision_score(y_te, preds, zero_division=0)),
            "recall": float(recall_score(y_te, preds, zero_division=0)),
            "f1": float(f1_score(y_te, preds, zero_division=0)),
            "feature_importance": [
                {"feature": k, "importance": round(v, 4)} for k, v in importance[:10]
            ],
        }
        return self.train_metrics

    def predict(self, features: dict[str, float]) -> tuple[float, str]:
        """预测补丁 KEEP 概率,返回 (prob_keep, decision)"""
        if not self.is_trained or self.model is None:
            raise NotTrainedError("模型未训练,先调用 train() 或 load()")
        row = align_features([features], self.feature_names)[0]
        prob = float(self.model.predict_proba([row])[0][1])
        return prob, "KEEP" if prob >= 0.5 else "REVERT"

    def save(self, path: str | Path) -> str:
        """pickle 持久化,返回路径字符串"""
        if not self.is_trained:
            raise NotTrainedError("未训练的模型无需保存")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            pickle.dump(
                {
                    "model": self.model,
                    "feature_names": self.feature_names,
                    "trained_at": self.trained_at,
                    "train_metrics": self.train_metrics,
                },
                f,
            )
        return str(p)

    @classmethod
    def load(cls, path: str | Path) -> PatchClassifier:
        """从磁盘加载模型(文件不存在 → NotTrainedError)"""
        p = Path(path)
        if not p.is_file():
            raise NotTrainedError(f"模型文件不存在: {p}")
        with open(p, "rb") as f:
            data = pickle.load(f)  # noqa: S301 — 受控产物,非不可信输入
        clf = cls()
        clf.model = data.get("model")
        clf.feature_names = list(data.get("feature_names", []))
        clf.trained_at = str(data.get("trained_at", ""))
        metrics = data.get("train_metrics", {})
        clf.train_metrics = metrics if isinstance(metrics, dict) else {}
        return clf
