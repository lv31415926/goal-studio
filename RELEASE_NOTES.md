# goal-studio v0.1.0 发布说明

**发布日期**: 2026-08-14
**版本**: 0.1.0 (Alpha)
**许可**: Apache-2.0

---

## 项目简介

**goal-studio** 是一个可视化 Loop 创作系统，把目标变成可复用产物的可视化创作循环。它将"起草-呈现-精化-验证"的棘轮循环与多模态（语音/文本/鼠标）节点编辑结合，让创作过程可视化、可对话、可回滚、可复用。

### 核心特性

- **Loop-First 创作循环** — Draft → Visualize → Refine → Verify 四阶段，每轮棘轮门控，分数只能升不能降
- **多模态节点编辑** — 语音说"把这个改成异步"，三信号融合算法精准定位节点
- **三层可视化画布** — L1 任务规划 / L2 Agent 分配 / L3 节点精化，语义缩放自适应
- **可插拔产物适配器** — 技能 / 代码 / 文档 / 工作流，实现 6 方法接口即可扩展
- **独立开源** — 核心引擎零耦合，可 `pip install` 独立使用，也可作为 maxfun skill 集成

---

## 快速开始

### 环境要求

- Python >= 3.11
- Node.js >= 20（仅 Web 界面需要）
- pnpm >= 8

### 安装

```bash
git clone https://github.com/goal-studio/goal-studio.git
cd goal-studio

# 安装 Python 核心与服务端
pip install -e packages/goal-studio-core
pip install -e packages/goal-studio-server
```

### 启动 Web 界面（开发模式，免认证）

```bash
# 终端 1: 启动后端
uvicorn goal_studio_server.app:create_app --factory --port 8000

# 终端 2: 启动前端
cd packages/goal-studio-web
pnpm install
pnpm run dev
```

访问 http://localhost:5173 即可使用。

> **说明**: 开发模式（默认）下认证关闭，无需配置任何 token 即可体验完整功能。仅在设置 `GOAL_STUDIO_ENV=production` 时才强制启用认证。

### CLI 用法

```bash
# 起草三层 DAG
goal-studio draft --goal "创建一个日报生成技能" --type skill

# 精化指定节点
goal-studio refine node-3 --patch "把执行方式改成异步"

# 三重门控验证
goal-studio verify

# 固化为产物
goal-studio build --output ./my-skill
```

### 示例项目

仓库自带三个可直接运行的示例：

```bash
python examples/create-skill/run.py    # 生成 skill 包（13 节点，71.3 PASS）
python examples/create-api/run.py      # 生成 API 项目（12 节点，86.4 PASS）
python examples/create-report/run.py   # 生成文档（13 节点，86.4 PASS）
```

---

## 本次发布包含的内容

### 新增功能

- **五层意图解析流水线** — 采集 → 对齐 → LLM 解析 → 节点引用消解 → 动作执行
- **三层可视化画布** — React Flow + 语音/文本/鼠标多模态融合
- **棘轮门控（RatchetGate）** — 质量分数单调递增，REVERT 自动回滚
- **可插拔产物适配器** — Skill / Code / Doc / Workflow 四种内置类型
- **REST + WebSocket 实时通信** — 增量解析结果实时推送
- **VitePress 文档站** — 8 页完整文档（getting-started / concepts / architecture / adapter-dev / intent-parser / ratchet-gate / cli / api）
- **maxfun-adapter 插件** — 与 maxfun skill 生态双向转换
- **Plugin SDK** — 适配器脚手架、加载器、验证器

### 生产部署加固（PR-S1 ~ PR-S3）

- 多阶段 Dockerfile 构建（web builder + server），非 root 用户运行
- `.env.example` 环境变量配置模板
- `SECURITY.md` 安全政策文档
- `/api/health/detail` 增强健康检查端点
- `/metrics` 端点支持独立鉴权（Prometheus 兼容）
- 会话删除时级联清理产物文件
- SPA fallback 路由（前端路由刷新不再 404）
- CORS / WebSocket Origin / 速率限制安全加固

### 本次发布前修复的阻塞问题

- **`vite.dev.config.ts` 缺少 API/WebSocket 代理配置** — 前端 dev 模式下 `/api` 和 `/ws` 请求无法转发到后端，现已修复
- **`getting-started.md` 包管理器不一致** — 文档使用 `npm` 但项目使用 `pnpm`，已统一

---

## 验证结果

本次发布前完成了全量验证，所有检查项通过：

| 检查项 | 结果 |
|--------|------|
| Python 编译检查 | ✅ 通过 |
| Server 单元测试 | ✅ 22 passed |
| Core 单元测试 | ✅ 11 passed |
| 前端 TypeScript 类型检查 | ✅ 无错误 |
| 前端单元测试 | ✅ 7 passed |
| dev 模式免 token 访问 | ✅ 正常 |
| 前端 SPA 可访问 | ✅ 正常 |
| API 代理（`/api`） | ✅ 正常返回 JSON |
| WebSocket 代理（`/ws`） | ✅ ping/pong 正常 |
| 完整创作流程 | ✅ create → draft(13 nodes) → refine → validate → build(code) → delete 全部通过 |

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+, Pydantic v2, Click, asyncio, FastAPI |
| 前端 | React 18, TypeScript 5, @xyflow/react, Zustand+Jotai, ELK.js, Tailwind v4 |
| 多模态 | Vercel AI SDK 7, Web Speech API |
| 文档 | VitePress |
| 部署 | Docker, Docker Compose |

---

## 项目结构

```
goal-studio/
├── packages/
│   ├── goal-studio-core/      # Python 核心引擎 (pip install)
│   ├── goal-studio-server/    # FastAPI 服务端
│   ├── goal-studio-web/       # React 前端 (pnpm install)
│   └── goal-studio-docs/      # VitePress 文档
├── examples/                  # 示例项目 (create-skill / create-api / create-report)
├── plugins/                   # 社区适配器 (maxfun-adapter)
├── .github/workflows/         # CI
├── Dockerfile                 # 多阶段构建
├── docker-compose.yml         # 生产部署编排
├── .env.example               # 环境变量配置模板
└── SECURITY.md                # 安全政策
```

---

## 已知限制

- **LLM 默认 Mock 模式** — 未配置 `OPENAI_API_KEY` 时使用 Mock 模式返回固定桩数据，需配置真实 API Key 才能体验 LLM 驱动的完整能力
- **Web Speech API 浏览器兼容性** — 语音输入需要 Chrome/Edge 浏览器，且要求 HTTPS 或 localhost 环境
- **单进程部署** — 当前速率限制与 Pipeline 缓存为进程内实现，多 worker 部署需替换为 Redis
- **认证为可选** — 开发模式默认关闭认证，生产部署需设置 `GOAL_STUDIO_ENV=production` 并配置 `GOAL_STUDIO_API_TOKEN`

---

## 配置 LLM（可选）

默认使用 Mock 模式，无需 API Key 即可体验流程。接入真实大模型：

```bash
export GOAL_STUDIO_LLM_PROVIDER="openai"      # mock | openai | anthropic
export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选
export GOAL_STUDIO_LLM_MODEL="gpt-4o-mini"          # 可选
```

---

## 部署

### Docker 快速部署

```bash
cp .env.example .env
# 编辑 .env，至少设置:
#   GOAL_STUDIO_ENV=production
#   GOAL_STUDIO_API_TOKEN=<强随机值>
#   GOAL_STUDIO_CORS_ORIGINS=https://your-domain.com

docker compose up -d --build
```

详见 [部署文档](packages/goal-studio-docs/docs/deployment.md)。

---

## 贡献

欢迎提交 Issue 和 PR！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 理论基础

- **DesignerlyLoop** (Cross) — 设计学循环理论
- **SkillOpt** (Microsoft) — 技能优化的 9 维评估体系
- **ParsingDST** (Wu et al.) — LLM 对话状态跟踪
- **POLAR** (Yonsei+KAIST) — 个性化实例接地

---

## License

Apache 2.0 © 2026 goal-studio contributors
