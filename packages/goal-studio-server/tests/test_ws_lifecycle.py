"""WS 生命周期与资源清理测试 (P-LOW-2-10)

覆盖:
  - WS 连接 → ping → pong 基础流程
  - 无效 JSON 错误响应
  - 未知消息类型错误响应
  - 会话不存在时连接被拒
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goal_studio_server.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构造隔离的 TestClient,SessionManager root 指向 tmp_path"""
    # 绕过 deps 的 lru_cache,让 get_session_manager 指向 tmp
    import goal_studio_server.deps as deps

    def _fake_mgr():
        from goal_studio.core.session_manager import SessionManager
        return SessionManager(root=tmp_path / "sessions")

    monkeypatch.setattr(deps, "get_session_manager", _fake_mgr)
    # 也需绕过 Depends 缓存:用新 app
    app = create_app()
    with TestClient(app) as c:
        yield c


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/sessions", json={"name": "ws-test", "goal": None})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_ws_ping_pong(client):
    """WS 连接 → ping → pong"""
    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "ping", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "pong"
        assert msg["session_id"] == sid


def test_ws_invalid_json(client):
    """无效 JSON 应返回 bad_json 错误"""
    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_text("{not valid json")
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "bad_json"


def test_ws_unknown_type(client):
    """未知消息类型应返回 bad_type 错误"""
    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "unknown_xyz", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "bad_type"


def test_ws_session_not_found(client):
    """连接不存在的会话应被拒(close code 4404)"""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/sessions/nonexistent-session") as ws:
            ws.receive_json()
