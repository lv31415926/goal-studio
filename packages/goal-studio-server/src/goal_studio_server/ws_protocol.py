"""WebSocket 消息协议

客户端 → 服务端:
  - intent_parse: 解析自然语言指令
  - voice_chunk: 流式 ASR chunk
  - mouse_select: 鼠标选中节点
  - mouse_hover: 鼠标悬停节点
  - ping: 心跳

服务端 → 客户端:
  - graph_update: 图更新
  - phase_change: 阶段变化
  - intent_parsed: 意图解析结果 (Layer 3)
  - node_resolved: 节点消解结果 (Layer 4)
  - action_executed: 动作执行结果 (Layer 5)
  - error: 错误
  - pong: 心跳响应
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# R3-13: WS 错误码集中定义(StrEnum 语义,便于复用与前端对齐)
class WSErrorCode(StrEnum):
    EMPTY_TEXT = "empty_text"
    PIPELINE_ERROR = "pipeline_error"
    AMBIGUITY = "ambiguity"
    BAD_JSON = "bad_json"
    BAD_TYPE = "bad_type"
    MSG_TOO_LARGE = "msg_too_large"
    RATE_LIMITED = "rate_limited"
    PIPELINE_INIT = "pipeline_init"
    SERVER_ERROR = "server_error"
    HANDLER_ERROR = "handler_error"
    TEXT_TOO_LONG = "text_too_long"
    SESSION_DELETED = "session_deleted"


class ClientMessage(BaseModel):
    """客户端→服务端消息基类"""
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# R3-12: 常见消息类型的 payload schema(可选,用于校验/文档化)
class IntentParsePayload(BaseModel):
    """intent_parse 消息 payload"""
    text: str = ""
    is_mobile: bool = False
    force: bool = False


class VoiceChunkPayload(BaseModel):
    """voice_chunk 消息 payload"""
    text: str
    is_final: bool = False
    confidence: float = 0.9


class ServerMessage(BaseModel):
    """服务端→客户端消息基类"""
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    timestamp: float = 0.0


def msg_graph_update(session_id: str, graph: dict[str, Any], version: int) -> ServerMessage:
    return ServerMessage(
        type="graph_update",
        payload={"graph": graph, "version": version},
        session_id=session_id,
    )


def msg_phase_change(session_id: str, phase: str) -> ServerMessage:
    return ServerMessage(
        type="phase_change",
        payload={"phase": phase},
        session_id=session_id,
    )


def msg_intent_parsed(session_id: str, intent: dict[str, Any]) -> ServerMessage:
    return ServerMessage(
        type="intent_parsed",
        payload={"intent": intent},
        session_id=session_id,
    )


def msg_node_resolved(session_id: str, resolve: dict[str, Any]) -> ServerMessage:
    return ServerMessage(
        type="node_resolved",
        payload={"resolve": resolve},
        session_id=session_id,
    )


def msg_action_executed(
    session_id: str, execution: dict[str, Any], graph: dict[str, Any] | None = None
) -> ServerMessage:
    return ServerMessage(
        type="action_executed",
        payload={"execution": execution, "graph": graph},
        session_id=session_id,
    )


def msg_error(session_id: str, error: str, code: str = "error") -> ServerMessage:
    return ServerMessage(
        type="error",
        payload={"error": error, "code": code},
        session_id=session_id,
    )


def msg_clarification_needed(
    session_id: str, message: str, options: list[str]
) -> ServerMessage:
    """R3-01: 歧义澄清消息(替代 ambiguity error,前端引导用户选择)"""
    return ServerMessage(
        type="clarification_needed",
        payload={"message": message, "options": options},
        session_id=session_id,
    )


def msg_pong(session_id: str) -> ServerMessage:
    return ServerMessage(type="pong", payload={}, session_id=session_id)
