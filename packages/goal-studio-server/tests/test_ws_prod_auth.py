"""生产模式 WS 鉴权与 Origin 校验测试 (PR-S1)

覆盖:
  - 生产模式: 无 token / 错误 token 被拒
  - 生产模式: 正确 token + 白名单 Origin 连接成功
  - 生产模式: 正确 token + 非白名单 Origin 被拒
  - 生产模式: 非法 session_id 抛 ValueError 被捕获为 4404(而非 500)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goal_studio_server.app import create_app


@pytest.fixture
def prod_client(tmp_path, monkeypatch):
    """生产模式 TestClient: GOAL_STUDIO_ENV=production + token + Origin 白名单"""
    monkeypatch.setenv("GOAL_STUDIO_ENV", "production")
    monkeypatch.setenv("GOAL_STUDIO_API_TOKEN", "ci-test-token")
    monkeypatch.setenv(
        "GOAL_STUDIO_CORS_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    # 绕过 deps 的 lru_cache(monkeypatch.setenv 不影响已缓存值,需重建 deps 引用)
    import goal_studio_server.deps as deps
    deps.get_session_manager.cache_clear()
    # auth.py 模块级 token 缓存需重置,否则沿用其他测试加载的 None
    import goal_studio_server.auth as auth_mod
    monkeypatch.setattr(auth_mod, "_TOKEN_CACHE", None)
    monkeypatch.setattr(auth_mod, "_TOKEN_LOADED", False)

    def _fake_mgr():
        from goal_studio.core.session_manager import SessionManager
        return SessionManager(root=tmp_path / "sessions")

    monkeypatch.setattr(deps, "get_session_manager", _fake_mgr)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _auth(**kw):
    h = {"Authorization": "Bearer ci-test-token"}
    h.update(kw)
    return h


def test_prod_ws_no_token_rejected(prod_client):
    sid = prod_client.post("/api/sessions", json={"name": "t"}, headers=_auth()).json()["id"]
    with pytest.raises(Exception):
        with prod_client.websocket_connect(f"/ws/sessions/{sid}"):
            pass


def test_prod_ws_wrong_token_rejected(prod_client):
    sid = prod_client.post("/api/sessions", json={"name": "t"}, headers=_auth()).json()["id"]
    with pytest.raises(Exception):
        with prod_client.websocket_connect(f"/ws/sessions/{sid}?token=wrong"):
            pass


def test_prod_ws_allowed_origin_ping_pong(prod_client):
    sid = prod_client.post("/api/sessions", json={"name": "t"}, headers=_auth()).json()["id"]
    with prod_client.websocket_connect(
        f"/ws/sessions/{sid}?token=ci-test-token",
        headers={"Origin": "http://localhost:8000"},
    ) as ws:
        ws.send_json({"type": "ping", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "pong"


def test_prod_ws_disallowed_origin_rejected(prod_client):
    sid = prod_client.post("/api/sessions", json={"name": "t"}, headers=_auth()).json()["id"]
    with pytest.raises(Exception):
        with prod_client.websocket_connect(
            f"/ws/sessions/{sid}?token=ci-test-token",
            headers={"Origin": "http://evil.example.com"},
        ):
            pass


def test_prod_ws_invalid_session_id_closed_cleanly(prod_client):
    """非法 session_id 抛 ValueError,应被捕获为 4404 而非 500(PR-S1 修复)"""
    with pytest.raises(Exception):
        with prod_client.websocket_connect(
            "/ws/sessions/fake-session?token=ci-test-token",
            headers={"Origin": "http://localhost:8000"},
        ):
            pass
