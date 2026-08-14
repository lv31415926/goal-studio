# Dockerfile — 多阶段构建: web builder + server (PR-S1)
# R5-10: OCI LABEL 定义在最终运行时阶段(server)内,见下方

# R5-05: TODO 锁定到具体 digest 以保证可复现性
# FROM node:20-slim@sha256:<node-digest>
FROM node:20-slim AS web-builder
WORKDIR /app
# R5-06: 统一使用 pnpm(与 CI/本地开发一致),移除 npm
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
COPY packages/goal-studio-web/package.json packages/goal-studio-web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY packages/goal-studio-web/ ./
RUN pnpm run build:app

# R5-05: TODO 锁定到具体 digest 以保证可复现性
# FROM python:3.12-slim@sha256:<python-digest>
FROM python:3.12-slim AS server
WORKDIR /app

# R5-10: OCI 标准镜像元数据
LABEL org.opencontainers.image.title="goal-studio" \
      org.opencontainers.image.source="https://github.com/goal-studio/goal-studio" \
      org.opencontainers.image.licenses="Apache-2.0"

# 安装运行时依赖: ca-certificates(HTTPS) + tzdata(时区)
# 健康检查使用 Python 标准库 urllib,不依赖 curl,减小镜像体积
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# 安装 Python 依赖(复制完整包目录: hatchling 构建需要 src/ 和 README.md)
COPY packages/goal-studio-core/ packages/goal-studio-core/
COPY packages/goal-studio-server/ packages/goal-studio-server/
# R5-02: 安装 [llm] extras(openai/anthropic),生产 LLM 接入必需
RUN pip install --no-cache-dir "./packages/goal-studio-core[llm]" ./packages/goal-studio-server

# 复制前端构建产物(容器内固定路径, 与 GOAL_STUDIO_WEB_DIST 对应)
COPY --from=web-builder /app/dist-app/ /app/packages/goal-studio-web/dist-app/

# 创建会话数据目录并设置权限
RUN mkdir -p /data/sessions /data/artifacts && chown -R appuser:appuser /data /app

# 切换到非 root 用户
USER appuser

ENV GOAL_STUDIO_ENV=production
ENV GOAL_STUDIO_SESSION_ROOT=/data/sessions
ENV GOAL_STUDIO_ARTIFACT_ROOT=/data/artifacts
ENV GOAL_STUDIO_WEB_DIST=/app/packages/goal-studio-web/dist-app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/api/health\", timeout=3)"]
# 请求体大小限制由应用层中间件实现(app.py body_size_limit_middleware, 1MB)
CMD ["uvicorn", "goal_studio_server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
