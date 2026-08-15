"""F4 metrics 埋点 + A1 interim throttle 清理测试"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

from goal_studio_server.app import create_app
from goal_studio_server.metrics import METRICS


class _MockLLMProvider(BaseLLMProvider):
    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return "mock-model"

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: object,
    ) -> LLMResponse:
        return LLMResponse(
            content=(
                '{"intent":"query","target_ref":null,"slots":{},'
                '"ambiguity_flag":false,"clarification_options":[]}'
            ),
            model="mock-model",
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))
    from goal_studio.llm import registry as llm_registry

    import goal_studio_server.deps as deps
    from goal_studio_server.deps import get_llm_provider, get_session_manager

    deps.get_session_manager.cache_clear()
    llm_registry.reset_llm()

    from goal_studio.core.session_manager import SessionManager

    app = create_app()
    app.dependency_overrides[get_llm_provider] = lambda: _MockLLMProvider()
    app.dependency_overrides[get_session_manager] = lambda: SessionManager(
        root=tmp_path / "sessions"
    )
    with TestClient(app) as c:
        yield c


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/sessions", json={"name": "metrics-test", "goal": None})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_ws_and_llm_metrics_counters(client):
    """F4: ws_connections_active / ws_messages_total / llm_calls_total 真实埋点"""
    sid = _create_session(client)
    active0 = METRICS["ws_connections_active"]
    msgs0 = METRICS["ws_messages_total"]
    llm0 = METRICS["llm_calls_total"]

    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        assert METRICS["ws_connections_active"] == active0 + 1
        ws.send_json({"type": "ping", "payload": {}})
        ws.receive_json()  # pong
        assert METRICS["ws_messages_total"] >= msgs0 + 1
        ws.send_json({"type": "intent_parse", "payload": {"text": "查询当前图"}})
        # 排空直到 action_executed
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "action_executed":
                break
        assert METRICS["llm_calls_total"] == llm0 + 1
    assert METRICS["ws_connections_active"] == active0


def test_interim_throttle_cleanup_on_disconnect(client):
    """A1: 会话最后一个 WS 断开后,_interim_throttle 不残留"""
    from goal_studio_server.routes.ws import _interim_throttle

    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json(
            {"type": "voice_chunk",
             "payload": {"text": "把第一个节点", "confidence": 0.5, "is_final": False}}
        )
        ws.receive_json()  # interim intent_parsed
        assert sid in _interim_throttle
    assert sid not in _interim_throttle
