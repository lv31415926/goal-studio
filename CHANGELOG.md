# Changelog

本文件记录 goal-studio 的显著变更。遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 生产部署加固 (PR-S1 ~ PR-S3)

#### Added (新增)
- 生产 Dockerfile 多阶段构建(web builder + server),非 root 用户运行
- `GOAL_STUDIO_WEB_DIST` 环境变量: 容器/wheel 部署下前端静态文件定位
- `.env.example` 环境变量配置模板
- `SECURITY.md` 安全政策与防御机制文档
- `/api/health/detail` 增强健康检查端点(版本/模式/启动时间)
- `/metrics` 端点支持 `GOAL_STUDIO_METRICS_TOKEN` 独立鉴权(可选)
- Docker 镜像构建 CI 验证 job (`docker-build`)
- Python wheel/sdist 构建与安装验证 CI job (`python-build`)

#### Changed (变更)
- CORS 白名单默认加入 `http://localhost:8000` / `http://127.0.0.1:8000`(生产 SPA 托管)
- 会话删除时级联清理该会话的产物文件 (`delete_session` → `cleanup_artifacts`)
- `/api/health` 返回体扩展(`version` / `mode` / `uptime_seconds`)
- `docker-compose.yml` 增加 `GOAL_STUDIO_ARTIFACT_ROOT` 挂载与资源限制
- Dockerfile 健康检查改用 `python` 标准库探测(移除对 curl 的依赖)

#### Fixed (修复)
- 前端 Docker 镜像无可用 UI 问题(现在由后端托管 SPA 静态文件)
- 缺少 `.dockerignore` 导致构建上下文携带密钥/构建产物
- mypy 严格模式 35 处类型错误(server + core)
- LogRecord `msg` 字段冲突导致的日志异常
- Ruff B008(FastAPI Depends)规则误报
- `/api/health/detail` 缺失 `require_auth` 依赖(无 token 可访问)
- `/metrics` 延迟样本输出为 Python 列表字面量, 修复为均值浮点(Prometheus 文本格式合法)

## [0.1.0] - 2026-08-13

### Added (新增)
- 首个发布版本: 可视化 Loop 创作系统
- 五层意图解析流水线(采集/对齐/LLM解析/节点消解/动作执行)
- 三层可视化画布(React Flow + 语音/文本/鼠标多模态)
- 棘轮门控(RatchetGate)质量保证
- 可插拔产物适配器(技能/代码/文档/工作流)
- REST + WebSocket 实时通信
- VitePress 文档站(8 页)
