"""FastAPI 应用主入口

REST API + WebSocket 端点,连接 React 前端与 goal-studio-core。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from goal_studio_server.auth import require_auth, require_metrics_auth
from goal_studio_server.deps import get_session_manager
from goal_studio_server.logging_config import setup_logging
from goal_studio_server.routes import adapters, sessions, studio, ws

_logger = logging.getLogger(__name__)

# PR-S3: 进程启动时间(用于健康检查 uptime)
_START_TIME = time.time()

# PR-Q: 轻量级 metrics 收集(不引入 prometheus_client 依赖)
_metrics: dict = {
    "http_requests_total": defaultdict(int),
    "http_request_duration_seconds": defaultdict(list),
    "ws_connections_active": 0,
    "ws_messages_total": 0,
    "llm_calls_total": 0,
    "llm_errors_total": 0,
}

# PR-ROUND-1/A-09: 路径参数模板化,避免 session_id 进入标签导致基数爆炸
_PATH_PARAM_PATTERN = re.compile(r"/[a-f0-9]{12}(?=/|$)")
_METRICS_MAX_SAMPLES = 100

# PR-ROUND-1/A-01: REST API 速率限制(令牌桶,基于 API Token 分桶)
# 单 worker 模式下内存计数器足够;多 worker 需替换为 Redis
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60.0  # 60 秒窗口
_RATE_LIMIT_NORMAL = 60    # 普通端点: 60 次/分钟
_RATE_LIMIT_LLM = 10       # LLM 触发端点: 10 次/分钟
_LLM_ENDPOINTS = {"/draft", "/refine", "/validate", "/build"}
# R1-04: 速率限制桶 LRU 上限,防止桶字典无限增长(token 哈希化后仍可能积累)
_RATE_LIMIT_MAX_BUCKETS = 10000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    # P-LOW-2-9: 启动时配置结构化日志
    setup_logging()
    _logger.info("app_starting")
    # 启动时初始化
    get_session_manager()

    # PR-Q: 优雅关闭标志
    app.state.shutting_down = False

    yield

    # PR-Q: 优雅关闭 — 等待活跃连接排空
    _logger.info("app_stopping", extra={"phase": "draining"})
    app.state.shutting_down = True

    # 清理 PipelineCache (PR-ROUND-1/B-07: 使用公共 clear 方法,不访问私有属性)
    try:
        from goal_studio_server.routes.ws import _pipelines
        _pipelines.clear()
    except Exception:  # noqa: BLE001
        _logger.warning("pipeline_cache_cleanup_failed", exc_info=True)

    # 给一点时间让活跃 WS 连接发送关闭帧
    await asyncio.sleep(0.5)
    _logger.info("app_stopped")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="goal-studio",
        description="可视化 Loop 创作系统 — REST + WebSocket",
        version="0.1.0",
        lifespan=lifespan,
    )

    # PR-G/G-8: CORS 环境变量化 + 收紧 methods/headers
    from goal_studio_server.auth import get_allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    )

    # R1-10: 安全响应头中间件(在 CORS 之后注入)
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # R3-14: REST 请求体大小限制中间件(1MB,避免大 payload 耗尽内存)
    _max_request_body = 1024 * 1024  # 1MB

    @app.middleware("http")
    async def body_size_limit_middleware(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _max_request_body:
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        return await call_next(request)

    # PR-Q: metrics 中间件 — 采集 QPS/延迟
    # PR-ROUND-1/A-09: 路径参数模板化(_PATH_PARAM_PATTERN 在模块级定义)
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        path = request.url.path
        method = request.method
        # A-09: 将 12 位十六进制 session_id 替换为 {id},避免标签无限增长
        normalized_path = _PATH_PARAM_PATTERN.sub("/{id}", path)
        label = f"{method}_{normalized_path}"
        _metrics["http_requests_total"][label] += 1
        durations = _metrics["http_request_duration_seconds"][label]
        durations.append(duration)
        # 只保留最近 100 个延迟样本
        if len(durations) > _METRICS_MAX_SAMPLES:
            del durations[:-_METRICS_MAX_SAMPLES]
        return response

    # PR-ROUND-1/A-01: REST API 速率限制(令牌桶,基于 API Token 分桶)
    # 常量在模块级定义(_RATE_LIMIT_* / _LLM_ENDPOINTS)
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        method = request.method
        # 仅限制写操作
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            return await call_next(request)
        # 提取 bucket key(基于 token 哈希或 IP)
        # R1-04: token 哈希化,避免明文 token 作为 dict key 泄露/被 dump
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            bucket_key = hashlib.sha256(auth_header[7:].strip().encode()).hexdigest()[:16]
        elif request.client:
            bucket_key = request.client.host
        else:
            bucket_key = "unknown"
        # 判断是否 LLM 端点
        endpoint_name = path.rstrip("/").rsplit("/", 1)[-1] if "/" in path else path
        limit = _RATE_LIMIT_LLM if endpoint_name in _LLM_ENDPOINTS else _RATE_LIMIT_NORMAL
        now = time.time()
        timestamps = _RATE_LIMIT_BUCKETS.setdefault(bucket_key, [])
        timestamps[:] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
        # R1-04: 桶数量上限,超过时清理空桶(防 LRU 失控)
        if len(_RATE_LIMIT_BUCKETS) > _RATE_LIMIT_MAX_BUCKETS:
            stale = [k for k, v in _RATE_LIMIT_BUCKETS.items() if not v]
            for k in stale[:100]:
                _RATE_LIMIT_BUCKETS.pop(k, None)
        if len(timestamps) >= limit:
            retry_after = int(_RATE_LIMIT_WINDOW - (now - timestamps[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        timestamps.append(now)
        return await call_next(request)

    # 注册路由
    app.include_router(
        sessions.router,
        prefix="/api/sessions",
        tags=["sessions"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(
        studio.router,
        prefix="/api/sessions/{session_id}",
        tags=["studio"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(
        adapters.router,
        prefix="/api/adapters",
        tags=["adapters"],
        dependencies=[Depends(require_auth)],
    )
    app.include_router(ws.router, prefix="/ws", tags=["websocket"])

    # PR-S3: 基础健康检查(无需鉴权, 供负载均衡/编排器探活)
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        # PR-ROUND-4/A-06: 不返回 mode 字段,避免向未鉴权用户泄露运行模式
        # mode 信息移至需鉴权的 /api/health/detail
        # R1-09: 移除 version 字段,未鉴权端点不暴露版本信息
        return {
            "status": "ok",
        }

    # PR-S3: 详细健康检查(需鉴权, 含运行时长)
    @app.get("/api/health/detail", dependencies=[Depends(require_auth)])
    async def health_detail() -> dict[str, Any]:
        from goal_studio_server.auth import is_production
        return {
            "status": "ok",
            "version": "0.1.0",
            "mode": "production" if is_production() else "dev",
            "uptime_seconds": round(time.time() - _START_TIME, 1),
        }

    # PR-Q: Prometheus 文本格式 metrics 端点
    # PR-S3: /metrics 端点鉴权(生产模式默认需 API Token, 可配 GOAL_STUDIO_METRICS_TOKEN)
    @app.get("/metrics", dependencies=[Depends(require_metrics_auth)])
    async def metrics():
        lines = []
        for key, val in _metrics.items():
            if isinstance(val, defaultdict):
                # 计数器(int) 直接输出; 时长样本(list) 输出均值
                for label, samples in val.items():
                    if isinstance(samples, list) and samples:
                        avg = sum(samples) / len(samples)
                        lines.append(f'{key}{{endpoint="{label}"}} {avg:.6f}')
                    else:
                        lines.append(f'{key}{{endpoint="{label}"}} {samples}')
            else:
                lines.append(f"{key} {val}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")

    # PR-O: 全局异常处理器 — 防止未捕获异常泄露内部信息
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        _logger.error("unhandled_exception", extra={"path": request.url.path}, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error"},
        )

    # PR-S1: 生产静态文件服务 — SPA 应用托管
    # 前端构建产物定位顺序(PR-S2):
    #   1. GOAL_STUDIO_WEB_DIST 环境变量(Docker 部署时 WORKDIR=/app, 指向固定路径)
    #   2. 源码模式相对路径 packages/goal-studio-web/dist-app/
    #      (wheel 安装到 site-packages 时 __file__ 相对路径不可用, 故需环境变量)
    # 如果都不存在(开发模式),跳过静态挂载,不阻断启动
    web_dist = Path(os.getenv("GOAL_STUDIO_WEB_DIST", ""))
    if not (web_dist.is_dir() and (web_dist / "index.html").is_file()):
        _web_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        web_dist = _web_root / "packages" / "goal-studio-web" / "dist-app"
    if web_dist.is_dir() and (web_dist / "index.html").is_file():
        # SPA fallback: 非 API/WS 路径如果文件不存在,返回 index.html(前端路由)
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # API/WS 路径不走 SPA fallback
            if full_path.startswith(("api/", "ws/", "metrics")):
                raise HTTPException(status_code=404, detail="Not Found")
            # 尝试返回静态文件
            file_path = web_dist / full_path
            if full_path and file_path.is_file():
                return FileResponse(str(file_path))
            # 文件不存在,返回 index.html(SPA 路由)
            index_path = web_dist / "index.html"
            if index_path.is_file():
                return FileResponse(str(index_path))
            raise HTTPException(status_code=404, detail="Not Found")

        app.mount("/assets", StaticFiles(directory=str(web_dist / "assets")), name="assets")
        _logger.info("static_mounted", extra={"path": str(web_dist)})
    else:
        _logger.warning(
            "static_not_found",
            extra={"path": str(web_dist), "detail": "前端 dist-app 不存在, 仅 API 模式运行"},
        )

    return app


app = create_app()
