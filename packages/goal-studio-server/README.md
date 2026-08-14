# goal-studio-server

FastAPI 后端 — REST + WebSocket 实时通信,连接 React 前端与 goal-studio-core。

## 架构

```
React 前端  ←WebSocket→  FastAPI 后端  →  goal-studio-core
     ↑                        ↓
     └──── graph_update ────┘
```

## REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /api/health | 健康检查 |
| GET  | /api/adapters | 列出适配器 |
| GET  | /api/sessions | 列出会话 |
| POST | /api/sessions | 创建会话 |
| GET  | /api/sessions/{id} | 获取会话详情 |
| DELETE | /api/sessions/{id} | 删除会话 |
| POST | /api/sessions/{id}/draft | 起草 DAG |
| POST | /api/sessions/{id}/refine | 精化补丁 |
| POST | /api/sessions/{id}/validate | 验证 |
| POST | /api/sessions/{id}/build | 构建产物 |

## WebSocket

`ws://host:port/ws/sessions/{session_id}`

### 客户端 → 服务端

```json
{"type": "intent_parse", "payload": {"text": "把这个改成异步"}}
{"type": "voice_chunk", "payload": {"text": "把", "confidence": 0.9, "is_final": false}}
{"type": "mouse_select", "payload": {"node_id": "l1-req"}}
{"type": "mouse_hover", "payload": {"node_id": "l1-req"}}
{"type": "ping"}
```

### 服务端 → 客户端

```json
{"type": "graph_update", "payload": {"graph": {...}, "version": 2}}
{"type": "phase_change", "payload": {"phase": "refine"}}
{"type": "intent_parsed", "payload": {"intent": {...}}}
{"type": "node_resolved", "payload": {"resolve": {...}}}
{"type": "action_executed", "payload": {"execution": {...}, "graph": {...}}}
{"type": "error", "payload": {"error": "...", "code": "..."}}
{"type": "pong"}
```

## 运行

```bash
# 安装
pip install -e ../goal-studio-core
pip install -e .

# 配置 LLM (默认 mock)
export GOAL_STUDIO_LLM_PROVIDER=mock

# 启动
uvicorn goal_studio_server.app:app --reload --port 8000
```
