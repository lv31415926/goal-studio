"""WebSocket 端点 — 实时双向通信

连接: ws://host:port/ws/sessions/{session_id}

客户端 → 服务端消息:
  {type: "intent_parse", payload: {text: "...", is_mobile: false}}
  {type: "voice_chunk", payload: {text: "...", confidence: 0.9, is_final: false}}
  {type: "mouse_select", payload: {node_id: "l1-xxx"}}
  {type: "mouse_hover", payload: {node_id: "l1-xxx" | null}}
  {type: "ping"}

服务端 → 客户端消息:
  {type: "graph_update", payload: {graph, version}}
  {type: "phase_change", payload: {phase}}
  {type: "intent_parsed", payload: {intent}}
  {type: "node_resolved", payload: {resolve}}
  {type: "action_executed", payload: {execution, graph}}
  {type: "error", payload: {error, code}}
  {type: "pong"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.session_manager import SessionManager, SessionPhase
from goal_studio.intent_parser.pipeline import IntentPipeline
from goal_studio.llm.base import BaseLLMProvider
from pydantic import ValidationError

from goal_studio_server.deps import (
    get_adapter_registry,
    get_llm_provider,
    get_session_manager,
)
from goal_studio_server.ws_protocol import (
    ClientMessage,
    msg_action_executed,
    msg_clarification_needed,
    msg_error,
    msg_graph_update,
    msg_intent_parsed,
    msg_node_resolved,
    msg_phase_change,
    msg_pong,
)

_logger = logging.getLogger(__name__)

router = APIRouter()


class PipelineCache:
    """会话级 IntentPipeline 缓存 — LRU + TTL + 线程安全 (P-LOW-2-1)

    解决 1000 次连断后 _pipelines dict 无限增长问题:
      - LRU: maxsize 超过时驱逐最旧 entry
      - TTL: 超过 ttl 秒未访问的 entry 过期(get 返回 None)
      - 线程安全: OrderedDict 非线程安全,所有操作加 threading.Lock
      - touch: WS finally 块调用,刷新活跃会话的访问时间

    独立审核修订 1: OrderedDict 的 move_to_end/popitem 非原子,必须加锁。
    """

    def __init__(self, maxsize: int = 64, ttl: float | None = None) -> None:
        self._store: OrderedDict[str, tuple[IntentPipeline, float]] = OrderedDict()
        self._maxsize = maxsize
        # TTL 默认 1800s,可由 GOAL_STUDIO_PIPELINE_TTL 环境变量配置(专家 2 建议)
        self._ttl = ttl if ttl is not None else float(
            os.getenv("GOAL_STUDIO_PIPELINE_TTL", "1800")
        )
        self._lock = threading.Lock()

    def get(self, session_id: str) -> IntentPipeline | None:
        with self._lock:
            if session_id not in self._store:
                return None
            pipe, ts = self._store[session_id]
            # TTL 过期检查
            if time.time() - ts > self._ttl:
                del self._store[session_id]
                return None
            self._store.move_to_end(session_id)
            return pipe

    def put(self, session_id: str, pipe: IntentPipeline) -> None:
        with self._lock:
            self._store[session_id] = (pipe, time.time())
            self._store.move_to_end(session_id)
            # LRU 驱逐
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    def touch(self, session_id: str) -> None:
        """刷新访问时间,避免活跃会话被 TTL 驱逐"""
        with self._lock:
            if session_id in self._store:
                pipe, _ = self._store[session_id]
                self._store[session_id] = (pipe, time.time())

    def cleanup(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)

    def clear(self) -> None:
        """清空所有缓存(PR-ROUND-1/B-07: 替代直接访问 _store 私有属性)"""
        with self._lock:
            self._store.clear()

    def __contains__(self, session_id: str) -> bool:
        return self.get(session_id) is not None

    def __len__(self) -> int:
        return len(self._store)


# 会话级 pipeline 缓存: LRU + TTL + 加锁(替代裸 dict)
_pipelines = PipelineCache(maxsize=64)

# PR-O: WebSocket 安全防护 — 速率限制 + 连接数控制
_ws_msg_timestamps: dict[str, list[float]] = {}  # connection_id → msg timestamps (rate limit)
_active_connections: dict[str, int] = {}  # session_id → active connection count
_MAX_CONNECTIONS_PER_SESSION = 3
_MAX_TOTAL_CONNECTIONS = 100
_WS_MAX_MSG_SIZE = 65536  # 64KB
_WS_RATE_LIMIT_PER_SEC = 10  # 每秒最多 10 条消息
# PR-ROUND-1/B-01: 连接计数器 asyncio.Lock,防止 check-then-act 竞态
_active_connections_lock = asyncio.Lock()
# R3-10: 会话级活跃 WS 连接注册表,供 delete_session 主动关闭(避免僵尸连接)
_session_connections: dict[str, set[WebSocket]] = {}


def _register_connection(session_id: str, ws: WebSocket) -> None:
    """R3-10: 注册活跃连接(accept 后调用)"""
    _session_connections.setdefault(session_id, set()).add(ws)


def _unregister_connection(session_id: str, ws: WebSocket) -> None:
    """R3-10: 注销活跃连接(断开时调用)"""
    conns = _session_connections.get(session_id)
    if conns:
        conns.discard(ws)
        if not conns:
            _session_connections.pop(session_id, None)


async def _pong_handler(ws: WebSocket) -> None:
    """R3-05: 服务端主动 ping 心跳,独立 task 解耦业务消息阻塞

    每 30s 发送一次 ping,避免长耗时 intent_parse 阻塞心跳。
    连接关闭时静默退出。
    """
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping"})
    except Exception:  # noqa: BLE001
        pass


def _get_or_create_pipeline(
    session_id: str,
    mgr: SessionManager,
    llm: BaseLLMProvider,
) -> IntentPipeline:
    """获取或创建会话的 IntentPipeline(P-LOW-2-1: 走 PipelineCache)"""
    cached = _pipelines.get(session_id)
    if cached is not None:
        # 同步最新图
        s = mgr.get(session_id)
        if s.graph:
            cached.update_graph(s.graph)
        cached.dialog_history = s.dialog
        return cached
    s = mgr.get(session_id)
    if not s.graph:
        from goal_studio.core.node_graph import NodeGraph
        graph = NodeGraph()
    else:
        graph = s.graph
    pipeline = IntentPipeline(graph=graph, llm=llm, dialog_history=s.dialog)
    _pipelines.put(session_id, pipeline)
    return pipeline


async def _send_json(ws: WebSocket, msg_dict: dict[str, Any]) -> None:
    """发送 JSON 消息"""
    msg_dict["timestamp"] = time.time()
    await ws.send_text(json.dumps(msg_dict, ensure_ascii=False, default=str))


async def _handle_intent_parse(
    ws: WebSocket,
    session_id: str,
    payload: dict[str, Any],
    pipeline: IntentPipeline,
    mgr: SessionManager,
) -> None:
    """处理 intent_parse 消息 — 五层流水线全流程"""
    text = payload.get("text", "")
    # PR-ROUND-1/A-03: text 必须是字符串
    if not isinstance(text, str):
        text = str(text) if text else ""
    # R1-13: text 长度上限,防止超长输入耗尽 LLM 配额/内存
    if len(text) > 10000:
        await _send_json(
            ws,
            msg_error(session_id, "text too long (max 10000 chars)", "text_too_long").model_dump(),
        )
        return
    is_mobile = bool(payload.get("is_mobile", False))
    force = bool(payload.get("force", False))  # 边界场景: 确认后强制执行
    if not text:
        _logger.warning("intent_parse_empty_text", extra={"session_id": session_id})
        await _send_json(ws, msg_error(session_id, "text is empty", "empty_text").model_dump())
        return
    _logger.info(
        "intent_parse_start",
        extra={
            "session_id": session_id,
            "text_len": len(text),
            "is_mobile": is_mobile,
            "force": force,
        },
    )

    # PR-M: 复用 SessionManager 的 per-session 锁,串行化同 session 的 pipeline 处理 + 状态更新
    s = mgr.get(session_id)
    lock = mgr._get_session_lock(session_id)
    loop = asyncio.get_event_loop()

    # 在线程池中持锁执行 pipeline.process()(同步 LLM 调用,避免阻塞事件循环)
    def _process_with_lock():
        with lock:
            # 同步最新图到 pipeline
            if s.graph:
                pipeline.update_graph(s.graph)
            pipeline.dialog_history = s.dialog
            # 推入文本信号
            pipeline.ingest_text(text)
            return pipeline.process(text=text, is_mobile=is_mobile, force=force)

    result = await loop.run_in_executor(None, _process_with_lock)

    # 推送 Layer 3 结果
    if result.intent:
        await _send_json(
            ws,
            msg_intent_parsed(session_id, result.intent.to_dict()).model_dump(),
        )

    # 推送 Layer 4 结果
    if result.resolve:
        await _send_json(
            ws,
            msg_node_resolved(session_id, _resolve_to_dict(result.resolve)).model_dump(),
        )

    # 推送 Layer 5 结果 + 图更新
    if result.execution:
        exec_dict = _execution_to_dict(result.execution)
        graph_dict = result.final_graph.to_dict() if result.final_graph else None
        # 边界场景: 删除级联确认 — 不更新图,等客户端确认后带 force 重新调用
        # R3-04: action_executed 不再内嵌 graph(graph 更新统一由 graph_update 消息承载)
        if getattr(result.execution, "needs_confirmation", False) and not force:
            await _send_json(
                ws, msg_action_executed(session_id, exec_dict).model_dump()
            )
            return
        if result.final_graph:
            # PR-M: 状态更新加锁,避免并发修改同一 session(mgr.save 内部 RLock 可重入,无死锁)
            with lock:
                s.update_graph(result.final_graph)
                s.phase = SessionPhase.REFINE
                s.add_dialog("user", text)
                assistant_msg = f"executed: {result.execution.reason or 'ok'}"
                # 边界场景: 多模态冲突告警
                if getattr(result.execution, "conflict_warning", ""):
                    assistant_msg += f" | ⚠ {result.execution.conflict_warning}"
                s.add_dialog("assistant", assistant_msg)
                mgr.save(s)
                # PR-ROUND-1/B-05: 锁内捕获 version,避免锁外读取时被并发修改
                graph_version = s.graph.version
            await _send_json(
                ws, msg_phase_change(session_id, s.phase.value).model_dump()
            )
        # R3-04: action_executed 不再携带 graph 字段(graph 更新由独立的 graph_update 消息承载)
        await _send_json(
            ws,
            msg_action_executed(session_id, exec_dict).model_dump(),
        )
        if graph_dict:
            await _send_json(
                ws,
                # PR-ROUND-1/B-05: 使用锁内捕获的 graph_version
                msg_graph_update(session_id, graph_dict, graph_version).model_dump(),
            )

    # 处理错误
    if result.error:
        await _send_json(ws, msg_error(session_id, result.error, "pipeline_error").model_dump())

    # R3-02: 歧义需要澄清 — 发送 clarification_needed 而非 error(前端引导用户选择)
    # 节点解析返回 clarification_options 时优先使用 resolve 的 reason/options,
    # 否则回退到 intent.ambiguity_flag + intent.clarification_options
    _needs_clarify = False
    _clarify_msg = "请澄清你指的是哪个节点"
    _clarify_options: list[str] = []
    if result.resolve and result.resolve.clarification_options:
        _needs_clarify = True
        _clarify_options = list(result.resolve.clarification_options)
        _clarify_msg = result.resolve.reason or _clarify_msg
    elif result.intent and getattr(result.intent, "ambiguity_flag", False):
        _needs_clarify = True
        _clarify_options = list(getattr(result.intent, "clarification_options", []) or [])
        _clarify_msg = "节点引用歧义,请明确指定"
    if _needs_clarify:
        await _send_json(
            ws,
            msg_clarification_needed(session_id, _clarify_msg, _clarify_options).model_dump(),
        )


# Phase 3: 会话级 interim 增量解析节流状态 — 避免每个 interim chunk 都触发 LLM
# {session_id: {"last_text": str, "last_ts": float}}
_interim_throttle: dict[str, dict[str, Any]] = {}
_INTERIM_MIN_INTERVAL = 0.3  # 秒


def cleanup_session(session_id: str) -> None:
    """清理会话级缓存(连接断开或会话删除时调用)"""
    # P-LOW-2-1: PipelineCache 用 cleanup 方法(非 pop)
    _pipelines.cleanup(session_id)
    _interim_throttle.pop(session_id, None)


async def _handle_voice_chunk(
    ws: WebSocket,
    session_id: str,
    payload: dict[str, Any],
    pipeline: IntentPipeline,
    mgr: SessionManager,
) -> None:
    """处理 voice_chunk — Phase 3 增量解析

    - interim chunk (is_final=False): 节流后调用 parse_incremental,推送低置信度预览
    - final chunk (is_final=True): 调用 parse_incremental (会走完整 parse),推送最终意图
    """
    text = payload.get("text", "")
    # PR-ROUND-1/A-03: 类型校验
    if not isinstance(text, str):
        text = str(text) if text else ""
    confidence = payload.get("confidence", 0.9)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.9
    confidence = max(0.0, min(1.0, confidence))
    is_final = bool(payload.get("is_final", False))
    if not text:
        return

    # 推入语音信号 (Layer 1 采集 → Layer 2 缓冲)
    pipeline.ingest_voice(text, confidence)

    # interim chunk 节流: 同一文本短时间内不重复解析,且全局间隔 >= _INTERIM_MIN_INTERVAL
    if not is_final:
        state = _interim_throttle.get(session_id, {"last_text": "", "last_ts": 0.0})
        now = time.time()
        if text == state["last_text"] or (now - state["last_ts"]) < _INTERIM_MIN_INTERVAL:
            return
        _interim_throttle[session_id] = {"last_text": text, "last_ts": now}

    loop = asyncio.get_event_loop()
    lock = mgr._get_session_lock(session_id)
    # PR-M: 复用 per-session 锁,串行化同 session 的 pipeline 读取(避免 process 并发修改 graph)
    def _parse_with_lock():
        with lock:
            # R1-12: 解析前同步最新图到 pipeline,避免使用过期 graph
            s = mgr.get(session_id)
            if s.graph:
                pipeline.update_graph(s.graph)
            return pipeline.parser.parse_incremental(
                text,
                canvas_summary=pipeline._build_canvas_summary(),
                dialog_history=pipeline.dialog_history,
            )
    intent = await loop.run_in_executor(None, _parse_with_lock)
    intent_dict = intent.to_dict()
    # 标记 ASR 置信度,供前端 Layer 4 消歧参考
    intent_dict.setdefault("asr_confidence", confidence)
    # Phase 3: interim chunk 即使解析完整也标记为预览,前端据此分流到 incrementalIntent
    # (final chunk 不加此标记,前端视为最终结果)
    if not is_final:
        intent_dict["is_interim"] = True
        # 若 parse_source 不是 incremental_* 前缀(如走完整 parse 返回 llm/heuristic),
        # 强制前缀化以保证前端分流逻辑一致
        src = intent_dict.get("parse_source", "")
        if not src.startswith("incremental_"):
            intent_dict["parse_source"] = f"incremental_{src}"
    else:
        # R3-03: is_final=True 时去除 parse_source 的 incremental_ 前缀,保证前端视为最终结果
        src = intent_dict.get("parse_source", "")
        if src.startswith("incremental_"):
            intent_dict["parse_source"] = src[len("incremental_"):]
    await _send_json(ws, msg_intent_parsed(session_id, intent_dict).model_dump())


async def _handle_mouse_select(
    ws: WebSocket,
    session_id: str,
    payload: dict[str, Any],
    pipeline: IntentPipeline,
) -> None:
    """处理 mouse_select"""
    node_id = payload.get("node_id")
    if node_id:
        pipeline.ingest_mouse_select(node_id)


async def _handle_mouse_hover(
    ws: WebSocket,
    session_id: str,
    payload: dict[str, Any],
    pipeline: IntentPipeline,
) -> None:
    """处理 mouse_hover"""
    node_id = payload.get("node_id")
    pipeline.ingest_mouse_hover(node_id)


@router.websocket("/sessions/{session_id}")
async def session_ws(
    ws: WebSocket,
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
    llm: BaseLLMProvider = Depends(get_llm_provider),
) -> None:
    """会话 WebSocket 端点"""
    # P-LOW-2-9: 生成本次连接的 connection_id 用于链路追踪(专家 5 建议)
    connection_id = uuid.uuid4().hex[:8]

    # 认证(accept 前)—— 与 REST 共用 verify_token
    from goal_studio_server.auth import is_origin_allowed, verify_token
    if not verify_token(ws.query_params.get("token")):
        _logger.warning(
            "ws_auth_failed",
            extra={"session_id": session_id, "connection_id": connection_id},
        )
        # PR-ROUND-3/D-02: 使用标准关闭码 1008(Policy Violation)
        await ws.close(code=1008, reason="Unauthorized")
        return

    # PR-G/G-7: WebSocket Origin 校验(防 CSWSH 跨站 WebSocket 劫持)
    origin = ws.headers.get("origin")
    if not is_origin_allowed(origin):
        _logger.warning(
            "ws_origin_rejected",
            extra={"session_id": session_id, "connection_id": connection_id, "origin": origin},
        )
        await ws.close(code=1008, reason="Origin not allowed")
        return

    # 验证会话存在
    # PR-S1: 非法 session_id(不匹配 12 位十六进制白名单)由 _validate_session_id 抛
    # ValueError,必须同样捕获并返回 4404,否则异常泄漏为 500。
    try:
        mgr.get(session_id)
    except (KeyError, ValueError):
        _logger.warning(
            "ws_session_not_found",
            extra={"session_id": session_id, "connection_id": connection_id},
        )
        await ws.close(code=1008, reason=f"Session {session_id} not found")
        return

    # PR-ROUND-1/B-01: 连接数控制加 asyncio.Lock,防止并发 accept 时 check-then-act 竞态
    # 临界区仅包含计数器检查+递增,accept 在锁外(避免持锁等待网络 IO)
    async with _active_connections_lock:
        _total = sum(_active_connections.values())
        if _total >= _MAX_TOTAL_CONNECTIONS:
            _logger.warning(
                "ws_server_overloaded",
                extra={"session_id": session_id, "connection_id": connection_id, "total": _total},
            )
            await ws.close(code=1013, reason="server overloaded")
            return
        _count = _active_connections.get(session_id, 0)
        if _count >= _MAX_CONNECTIONS_PER_SESSION:
            _logger.warning(
                "ws_too_many_connections",
                extra={"session_id": session_id, "connection_id": connection_id, "count": _count},
            )
            await ws.close(code=1013, reason="too many connections for this session")
            return
        _active_connections[session_id] = _count + 1

    await ws.accept()
    # R3-10: 注册活跃连接,供 delete_session 主动关闭
    _register_connection(session_id, ws)
    _logger.info("ws_connected", extra={"session_id": session_id, "connection_id": connection_id})

    try:
        pipeline = _get_or_create_pipeline(session_id, mgr, llm)
    except Exception as e:  # noqa: BLE001
        _logger.error(
            "ws_pipeline_init_failed",
            extra={"session_id": session_id, "connection_id": connection_id, "err": str(e)},
            exc_info=True,
        )
        # PR-G/G-10: 异常脱敏 — 不向客户端泄露内部错误细节
        await _send_json(
            ws,
            msg_error(session_id, "pipeline initialization failed", "pipeline_init").model_dump(),
        )
        # R1-15: 使用标准关闭码 1011(internal error) 而非自定义 4500
        await ws.close(code=1011, reason="pipeline init failed")
        # R1-14: 递减计数纳入锁临界区,防止 check-then-act 竞态
        async with _active_connections_lock:
            _active_connections[session_id] = max(0, _active_connections.get(session_id, 0) - 1)
            if _active_connections.get(session_id, 0) == 0:
                _active_connections.pop(session_id, None)
        # R3-10: 注销连接
        _unregister_connection(session_id, ws)
        return

    # R3-05: 独立心跳 task,服务端主动 ping,避免被业务消息阻塞
    pong_task = asyncio.create_task(_pong_handler(ws))
    try:
        while True:
            # PR-ROUND-3/D-01: 服务端超时 300s,客户端假死时自动关闭释放连接
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=300.0)
            except TimeoutError:
                _logger.info(
                    "ws_idle_timeout",
                    extra={"session_id": session_id, "connection_id": connection_id},
                )
                break
            # PR-O: 消息大小限制 (64KB)
            if len(raw) > _WS_MAX_MSG_SIZE:
                await _send_json(
                    ws,
                    msg_error(session_id, "message too large", "msg_too_large").model_dump(),
                )
                continue
            # PR-O: per-connection 速率限制 (每秒最多 10 条)
            now = time.time()
            timestamps = _ws_msg_timestamps.setdefault(connection_id, [])
            timestamps[:] = [t for t in timestamps if now - t < 1.0]
            if len(timestamps) >= _WS_RATE_LIMIT_PER_SEC:
                await _send_json(
                    ws,
                    msg_error(session_id, "rate limit exceeded", "rate_limited").model_dump(),
                )
                continue
            timestamps.append(now)
            try:
                data = json.loads(raw)
                # PR-ROUND-4/D-06: 使用 ClientMessage 模型校验入站消息结构(type + payload)
                # PR-ROUND-1/A-03: model_validate 会拒绝顶层非 dict / type 缺失 / payload 非 dict
                msg = ClientMessage.model_validate(data)
                msg_type = msg.type
                payload = msg.payload
            except json.JSONDecodeError:
                await _send_json(
                    ws, msg_error(session_id, "invalid JSON", "bad_json").model_dump()
                )
                continue
            except ValidationError:
                # model_validate 失败: 顶层非 dict / type 缺失 / payload 非 dict
                await _send_json(
                    ws,
                    msg_error(
                        session_id, "invalid message format", "bad_type"
                    ).model_dump(),
                )
                continue

            # R1-11: 消息 handler 异常隔离 — 单条消息异常不杀连接,记录并继续
            try:
                if msg_type == "intent_parse":
                    await _handle_intent_parse(ws, session_id, payload, pipeline, mgr)
                elif msg_type == "voice_chunk":
                    await _handle_voice_chunk(ws, session_id, payload, pipeline, mgr)
                elif msg_type == "mouse_select":
                    await _handle_mouse_select(ws, session_id, payload, pipeline)
                elif msg_type == "mouse_hover":
                    await _handle_mouse_hover(ws, session_id, payload, pipeline)
                elif msg_type == "ping":
                    await _send_json(ws, msg_pong(session_id).model_dump())
                else:
                    await _send_json(
                        ws,
                        msg_error(session_id, f"unknown type: {msg_type}", "bad_type").model_dump(),
                    )
            except Exception as e:
                _logger.error(
                    "ws_handler_error",
                    extra={"session_id": session_id, "msg_type": msg_type},
                    exc_info=True,
                )
                await _send_json(
                    ws,
                    msg_error(
                        session_id, f"handler error: {type(e).__name__}", "handler_error"
                    ).model_dump(),
                )
                continue  # 保持连接存活
    except WebSocketDisconnect:
        _logger.info(
            "ws_disconnected",
            extra={"session_id": session_id, "connection_id": connection_id},
        )
    except Exception as e:  # noqa: BLE001
        # P-LOW-2-9: 服务端记录完整异常,客户端只收通用错误(不泄露内部细节)
        _logger.error(
            "ws_session_error",
            extra={"session_id": session_id, "connection_id": connection_id, "err": str(e)},
            exc_info=True,
        )
        try:
            await _send_json(
                ws,
                msg_error(session_id, "internal error", "server_error").model_dump(),
            )
        except Exception:  # noqa: BLE001
            _logger.error(
                "ws_send_error_failed",
                extra={"session_id": session_id, "connection_id": connection_id},
            )
    finally:
        # R3-05: 取消心跳 task
        pong_task.cancel()
        # P-LOW-2-1: 刷新 pipeline TTL,活跃会话不被驱逐;保留以便重连恢复
        _pipelines.touch(session_id)
        # R1-14: 释放连接计数纳入锁临界区,防止 check-then-act 竞态
        async with _active_connections_lock:
            _active_connections[session_id] = max(0, _active_connections.get(session_id, 0) - 1)
            if _active_connections.get(session_id, 0) == 0:
                _active_connections.pop(session_id, None)
        # R3-10: 注销活跃连接
        _unregister_connection(session_id, ws)
        _ws_msg_timestamps.pop(connection_id, None)
        _logger.info("ws_closed", extra={"session_id": session_id, "connection_id": connection_id})


# === 辅助序列化 ===

def _resolve_to_dict(resolve: Any) -> dict[str, Any]:
    """ResolveResult → dict"""
    return {
        "ok": resolve.ok,
        "node_id": resolve.node_id,
        "reason": resolve.reason,
        "clarification_options": resolve.clarification_options,
        "confidence": resolve.confidence,
    }


def _execution_to_dict(execution: Any) -> dict[str, Any]:
    """ExecutionResult → dict"""
    patch_dict = None
    if execution.patch:
        patch_dict = {
            "op": execution.patch.op.value,
            "target_node_id": execution.patch.target_node_id,
            "target_node_id_2": execution.patch.target_node_id_2,
            "slots": execution.patch.slots,
        }
    return {
        "ok": execution.ok,
        "reason": getattr(execution, "reason", ""),
        "undo_available": getattr(execution, "undo_available", False),
        "patch": patch_dict,
        # 边界场景字段
        "needs_confirmation": getattr(execution, "needs_confirmation", False),
        "confirmation_context": getattr(execution, "confirmation_context", {}),
        "cycle_path": getattr(execution, "cycle_path", None),
        "conflict_warning": getattr(execution, "conflict_warning", ""),
    }
