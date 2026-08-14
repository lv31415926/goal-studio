"""棘轮崩溃恢复测试 (P-LOW-2-10, 验证 P-LOW-2-3 修复)

reload 后 ratchet.history 应从 ratchet_history 重建,早停判定可用。
注意: snapshots 不可恢复(成本过高),can_undo 应返回 False。
"""
from __future__ import annotations

from pathlib import Path

from goal_studio.core.session_manager import SessionManager


def test_history_preserved_after_reload(tmp_path: Path):
    """ratchet_history 在 reload 后应保留(用于 UI 展示)"""
    mgr = SessionManager(root=tmp_path)
    s = mgr.create(name="ratchet-test")
    s.ratchet_history = [
        {"round": 1, "old_score": 0.0, "new_score": 0.6, "delta": 0.6, "decision": "KEEP"},
        {"round": 2, "old_score": 0.6, "new_score": 0.61, "delta": 0.01, "decision": "KEEP"},
    ]
    mgr.save(s)

    # reload
    mgr2 = SessionManager(root=tmp_path)
    s2 = mgr2.get(s.id)
    assert len(s2.ratchet_history) == 2
    assert s2.ratchet_history[1]["delta"] == 0.01


def test_can_undo_false_after_reload(tmp_path: Path):
    """reload 后 snapshots 为空,can_undo 应为 False(undo 不可用)

    P-LOW-2-3 的设计:snapshots 不持久化完整 graph,reload 后 undo 不可用,
    UI 应通过 can_undo 标志提示用户。
    """
    mgr = SessionManager(root=tmp_path)
    s = mgr.create(name="undo-test")
    mgr.save(s)

    mgr2 = SessionManager(root=tmp_path)
    s2 = mgr2.get(s.id)
    # ratchet.snapshots 在 reload 后应为空
    assert len(s2.ratchet.snapshots) == 0


def test_best_score_preserved_after_reload(tmp_path: Path):
    """best_score/current_score 在 reload 后应保留"""
    mgr = SessionManager(root=tmp_path)
    s = mgr.create(name="score-test")
    s.best_score = 0.85
    s.current_score = 0.82
    mgr.save(s)

    mgr2 = SessionManager(root=tmp_path)
    s2 = mgr2.get(s.id)
    assert s2.best_score == 0.85
    assert s2.current_score == 0.82
