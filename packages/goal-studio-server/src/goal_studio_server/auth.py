"""轻量级 Token 认证(REST + WebSocket 统一)

通过环境变量 GOAL_STUDIO_API_TOKEN 控制:
  - 未设置(开发模式): 认证关闭,所有请求放行
  - 已设置: 要求 Bearer token 或 ?token= query 参数
"""
from __future__ import annotations

import hmac
import logging
import os
import time

from fastapi import Header, HTTPException, Query, status

_logger = logging.getLogger(__name__)

# R1-07: Token 缓存支持 TTL(60s)重新读取
_TOKEN_CACHE: str | None = None
_TOKEN_LOADED_AT: float = 0.0
_TOKEN_TTL = 60.0  # 60秒 TTL
# 保留 _TOKEN_LOADED 用于向后兼容(测试 monkeypatch 重置缓存:设为 False 强制下次重读)
_TOKEN_LOADED = False


def _get_configured_token() -> str | None:
    """读取环境变量配置的 token(带 TTL 缓存)

    R1-07: 布尔标志改为时间戳,_TOKEN_LOADED_AT 超过 TTL(60s)后重新读取环境变量,
    支持 token 轮换无需重启进程。保留 _TOKEN_LOADED=False 作为强制重读信号(测试兼容)。
    """
    global _TOKEN_CACHE, _TOKEN_LOADED_AT, _TOKEN_LOADED
    now = time.time()
    if (
        not _TOKEN_LOADED
        or not _TOKEN_LOADED_AT
        or (now - _TOKEN_LOADED_AT > _TOKEN_TTL)
    ):
        _TOKEN_CACHE = os.getenv("GOAL_STUDIO_API_TOKEN") or None
        _TOKEN_LOADED_AT = now
        _TOKEN_LOADED = True
    return _TOKEN_CACHE


def is_auth_enabled() -> bool:
    """认证是否启用"""
    return _get_configured_token() is not None


def is_production() -> bool:
    """是否为生产环境(PR-G/G-6: 生产环境强制认证)"""
    return os.getenv("GOAL_STUDIO_ENV", "dev").lower() == "production"


def verify_token(provided: str | None) -> bool:
    """统一校验入口(REST 与 WS 共用)

    - dev 模式且未配置 token: 一律放行
    - production 模式: 必须配置 token 且匹配(fail-closed,未配置则拒绝所有)
    - 已配置 token: hmac.compare_digest 常时比较

    PR-ROUND-1/A-08: provided 为 None 时由 `if not provided` 拦截返回 False,
    不会进入 hmac.compare_digest(None, str) 抛 TypeError。
    """
    expected = _get_configured_token()
    if expected is None:
        # PR-G/G-6: 生产环境 fail-closed — 未配置 token 时拒绝所有请求
        if not is_production():
            # PR-O: dev 模式跳过认证时记录警告
            _logger.warning("auth_disabled_in_dev", extra={"path": "verify_token"})
            return True
        return False
    # A-08: None 或空 token 直接拒绝,不进入 compare_digest(防 TypeError)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def get_allowed_origins() -> list[str]:
    """获取允许的 Origin 列表(PR-G/G-7, G-8 共用)

    PR-S1: 默认白名单同时包含开发端口(5173)与生产静态托管端口(8000),
    因为 SPA 由同一 FastAPI 进程在 8000 端口托管,浏览器 Origin 为
    http://localhost:8000,必须放行否则生产部署 WebSocket 会被误拒。
    """
    raw = os.getenv("GOAL_STUDIO_CORS_ORIGINS", "")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]


def is_origin_allowed(origin: str | None) -> bool:
    """校验 Origin 是否允许(PR-G/G-7: WebSocket Origin 校验)

    R1-06: dev 模式也至少校验 Origin 属于 loopback 或 None(非浏览器客户端),
    避免开发态完全放开 Origin 被 CSWSH 利用。
    - dev 模式: 放行 None(非浏览器客户端)和 loopback Origin
    - production 模式: 无 Origin 拒绝,有 Origin 必须在白名单内
    """
    if not is_production():
        if origin is None:
            return True
        return (
            origin.startswith("http://localhost")
            or origin.startswith("http://127.0.0.1")
            or origin.startswith("http://[::1]")
        )
    allowed = get_allowed_origins()
    if origin is None:
        return False
    return origin in allowed


async def require_auth(
    authorization: str | None = Header(default=None),
) -> None:
    """REST 路由级依赖

    PR-ROUND-4/A-07: 仅接受 Authorization: Bearer 头,不接受 ?token= query 参数,
    避免 uvicorn access log 记录 query string 导致 token 泄露。
    WebSocket 仍通过 ws.query_params.get("token") 直接传递(WS 无 access log)。
    """
    provided = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not verify_token(provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# PR-S3: Metrics 端点独立鉴权
# GOAL_STUDIO_METRICS_TOKEN 可单独配置 Prometheus 抓取凭据;
# 未配置时回退到 API Token(生产模式 fail-closed)。
# R1-07: 同样使用 TTL(60s)重新读取,支持 metrics token 轮换
_METRICS_TOKEN_CACHE: str | None = None
_METRICS_TOKEN_LOADED_AT: float = 0.0
_METRICS_TOKEN_LOADED = False


def _get_metrics_token() -> str | None:
    """读取 GOAL_STUDIO_METRICS_TOKEN(带 TTL 缓存)"""
    global _METRICS_TOKEN_CACHE, _METRICS_TOKEN_LOADED_AT, _METRICS_TOKEN_LOADED
    now = time.time()
    if (
        not _METRICS_TOKEN_LOADED
        or not _METRICS_TOKEN_LOADED_AT
        or (now - _METRICS_TOKEN_LOADED_AT > _TOKEN_TTL)
    ):
        _METRICS_TOKEN_CACHE = os.getenv("GOAL_STUDIO_METRICS_TOKEN") or None
        _METRICS_TOKEN_LOADED_AT = now
        _METRICS_TOKEN_LOADED = True
    return _METRICS_TOKEN_CACHE


def verify_metrics_token(provided: str | None) -> bool:
    """Metrics 端点鉴权

    - 配置了 GOAL_STUDIO_METRICS_TOKEN: 校验该 token
    - 未配置: 回退到 API Token 校验(生产模式 fail-closed)
    """
    expected = _get_metrics_token()
    if expected is not None:
        if not provided:
            return False
        return hmac.compare_digest(provided, expected)
    return verify_token(provided)


async def require_metrics_auth(
    authorization: str | None = Header(default=None),
) -> None:
    """Metrics 端点依赖(PR-ROUND-4/A-07: 仅接受 Authorization 头)"""
    provided = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not verify_metrics_token(provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing metrics token",
            headers={"WWW-Authenticate": "Bearer"},
        )
