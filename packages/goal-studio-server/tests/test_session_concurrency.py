"""SessionManager 并发安全测试 (P-LOW-2-10, 验证 P-LOW-2-4 锁机制)

50 线程并发保存同一会话,验证文件不损坏且可正常 reload。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from goal_studio.core.session_manager import Session, SessionManager


def test_concurrent_save_same_session(tmp_path: Path):
    """50 线程 × 20 次保存同一会话,文件不损坏"""
    mgr = SessionManager(root=tmp_path)
    s = mgr.create(name="concurrent-test")
    errors: list[Exception] = []

    def write_loop():
        try:
            for _ in range(20):
                mgr.save(s)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=write_loop) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写失败: {errors[:3]}"

    # 验证文件可正常加载
    raw = json.loads((tmp_path / f"{s.id}.json").read_text(encoding="utf-8"))
    reloaded = Session.from_dict(raw)
    assert reloaded.id == s.id
    assert reloaded.name == "concurrent-test"


def test_concurrent_create_distinct_sessions(tmp_path: Path):
    """20 线程并发创建不同会话,ID 不冲突"""
    mgr = SessionManager(root=tmp_path)
    created_ids: list[str] = []
    lock = threading.Lock()
    errors: list[Exception] = []

    def create_one():
        try:
            s = mgr.create(name="distinct-test")
            with lock:
                created_ids.append(s.id)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=create_one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发创建失败: {errors[:3]}"
    assert len(created_ids) == 20
    assert len(set(created_ids)) == 20  # ID 全部唯一
