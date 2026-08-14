"""goal-studio FastAPI 后端

REST + WebSocket 实时通信,连接 React 前端与 goal-studio-core。
"""
from goal_studio_server.app import create_app

__all__ = ["create_app"]
