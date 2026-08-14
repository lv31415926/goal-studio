"""WS intent_parse 处理器 mock 测试 (F-01p)

覆盖:
  - 空 text -> error(empty_text)
  - 有效 text + mock LLM -> 收到响应(intent_parsed + action_executed)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

from goal_studio_server.app import create_app


class _MockLLMProvider(BaseLLMProvider):
    """Mock LLM -- 返回固定的 query 意图 JSON"""

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
    """构造隔离的 TestClient,LLM provider 替换为 mock"""
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))

    import goal_studio_server.deps as deps
    from goal_studio_server.deps import get_llm_provider, get_session_manager

    # 清除 lru_cache
    deps.get_session_manager.cache_clear()
    deps.get_llm_provider.cache_clear()

    # Mock LLM: 使用 FastAPI dependency_overrides 覆盖 get_llm_provider
    mock_llm = _MockLLMProvider()
    from goal_studio.core.session_manager import SessionManager

    app = create_app()
    # PR-ROUND-4/F-01p: 使用 FastAPI 官方依赖覆盖机制注入 mock LLM
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    app.dependency_overrides[get_session_manager] = lambda: SessionManager(
        root=tmp_path / "sessions"
    )
    with TestClient(app) as c:
        yield c


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/sessions", json={"name": "intent-test", "goal": None})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_ws_intent_parse_empty_text(client):
    """空 text -> error(empty_text)"""
    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "intent_parse", "payload": {"text": ""}})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["payload"]["code"] == "empty_text"


def test_ws_intent_parse_with_mock_llm(client):
    """有效 text + mock LLM -> 收到响应(intent_parsed + action_executed)"""
    sid = _create_session(client)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        # "查询当前状态" 不匹配任何启发式规则,会调用 mock LLM
        ws.send_json(
            {"type": "intent_parse", "payload": {"text": "查询当前状态"}}
        )
        # 接收第一条响应(mock LLM 返回 query 意图,handler 先推送 intent_parsed)
        msg = ws.receive_json()
        assert msg["type"] == "intent_parsed", f"unexpected: {msg.get('type')}"
        assert msg["session_id"] == sid
        # intent 应为 query
        intent = msg["payload"]["intent"]
        assert intent["intent"] == "query"
        # 接收第二条响应(action_executed)
        msg2 = ws.receive_json()
        assert msg2["type"] == "action_executed", f"unexpected: {msg2.get('type')}"
