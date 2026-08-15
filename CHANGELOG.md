# Changelog

本文件记录 goal-studio 的显著变更。遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.3.0] - 2026-08-15

### 缺陷核查修复 (2026-08-15 全量代码审查)

#### Fixed (修复)
- P0-1: CLI `verify` 与 server `/validate` 棘轮状态分叉 — verify 改用会话级 ratchet 并深拷贝双写 `best_graph`,消除 CLI 验证后 Web build 拿到旧图的缺陷
- P0-2: 两个包同名 `tests/__init__.py` 导致仓库根目录 pytest 收集失败 — 移除包标记,新增根 `pytest.ini` 统一入口
- P1-3: ws/studio 路由 4 处访问私有 `SessionManager._get_session_lock` — 全部改用公共 `session_lock()` 上下文管理器
- A1: `_session_locks` 锁字典随会话数单调增长 — 引入持有者计数 + 超上限(256)清扫空闲锁;WS 断连后清理 `_interim_throttle` 残留
- C1: CLI `serve` 占位 stub 实现为真实 uvicorn 启动
- C3: `NodeGraph.add_edge` 同 id 去重由 O(n) 扫描改为 `_edge_ids` 集合 O(1)
- C4: `PipelineCache.__len__` 补持锁,与类内其他操作一致
- C5: OpenAI provider 空 content(推理模型 max_tokens 耗尽)不再触发第二次无效 LLM 调用,新增 `EmptyLLMContentError`
- C6: 前端 `useSession` 抽取 `connectWs`,消除 createAndDraft/loadSession 约 40 行重复
- C7: `apply_ratchet_decision` 生产 assert 改为显式 RuntimeError
- F2: CLI `build` 新增 `--dry-run`(只打印文件清单,不落盘不导出)
- F3: 移除死枚举 `SessionPhase.PAUSED`,`_missing_` 兼容历史持久化数据
- F4: `ws_connections_active` / `ws_messages_total` / `llm_calls_total` / `llm_errors_total` 四个空指标完成真实埋点(metrics 注册表迁入独立模块)
- F5: `examples/*/output/` 构建产物移出 git 跟踪(.gitignore + .gitkeep)

#### Changed (变更)
- 覆盖率门禁 60% → 65%(core/server 实测 67%)
- CI 新增 `e2e-examples` job: 三个示例全链路离线验证
- 版本号 0.1.0 → 0.3.0(Phase 3 完成对齐)

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
