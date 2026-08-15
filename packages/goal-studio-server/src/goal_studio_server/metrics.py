"""进程内轻量 metrics 注册表

F4: 从 app.py 抽离为独立模块,供 app 中间件与 ws/studio 路由共享埋点,
避免 ws.py 反向 import app 造成循环依赖。

指标语义:
  http_requests_total / http_request_duration_seconds — app.py 中间件埋点
  ws_connections_active   — ws.py 连接 accept/close 时增减
  ws_messages_total       — ws.py 每条通过校验的入站消息 +1
  llm_calls_total         — 触发 LLM 流水线的端点调用数(draft/refine/validate/build/intent_parse)
  llm_errors_total        — 上述调用中出错(异常或 result.error)的次数
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

METRICS: dict[str, Any] = {
    "http_requests_total": defaultdict(int),
    "http_request_duration_seconds": defaultdict(list),
    "ws_connections_active": 0,
    "ws_messages_total": 0,
    "llm_calls_total": 0,
    "llm_errors_total": 0,
}
