---
outline: [2, 3]
---

# 快速开始

本指南将帮助你在几分钟内安装并运行 goal-studio,完成第一个可视化 Loop 工作流。

## 环境要求

- Python >= 3.11
- Node.js >= 20(仅 Web 界面需要)
- Git

## 安装

### 从源码安装

goal-studio 目前通过源码安装。克隆仓库后使用 `pip` 安装:

```bash
git clone https://github.com/goal-studio/goal-studio.git
cd goal-studio
pip install -e packages/goal-studio-core
```

### 开发模式安装

如果你需要参与开发,推荐使用开发模式并安装完整依赖:

```bash
git clone https://github.com/goal-studio/goal-studio.git
cd goal-studio

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 开发模式安装(核心 + 服务端,包含测试与文档依赖)
pip install -e packages/goal-studio-core[dev]
pip install -e packages/goal-studio-server
```

::: tip 验证安装
安装完成后,执行以下命令确认 CLI 可用:

```bash
goal-studio --version
```
:::

## 第一个示例

仓库自带 `examples/create-skill/run.py` 示例,演示如何从一个自然语言目标生成完整的 skill 产物。

```bash
python examples/create-skill/run.py
```

该示例会:

1. 读取一个 `GoalContract`(目标契约)
2. 经五层意图解析生成三层 DAG
3. 调用 `skill` 适配器产出最终 skill 包

运行结束后,产物会输出到 `examples/create-skill/output/` 目录。

::: warning 首次运行
首次运行时默认使用 Mock LLM 模式,无需真实 API Key 即可体验完整流程。若需接入真实大模型,请参考 [配置 LLM](#配置-llm) 小节。
:::

## CLI 基本用法

goal-studio 提供统一的 `goal-studio` 命令行工具,覆盖从初始化到构建的完整生命周期。

### 初始化项目

```bash
goal-studio init my-project
cd my-project
```

该命令会在 `my-project/` 下生成 `goal.yaml`、`.goal-studio/` 配置目录与示例适配器。

### 生成草稿 DAG

```bash
goal-studio draft --goal "创建一个可以总结网页内容的 skill"
```

`draft` 会读取 `goal.yaml` 中的 `GoalContract`,经意图解析后生成三层 DAG 草稿,写入 `.goal-studio/draft.json`。

### 可视化预览

```bash
goal-studio view
```

`view` 会启动本地静态预览服务,在浏览器中渲染三层 DAG 结构,方便排查节点关系。

### 精化节点

```bash
goal-studio refine --node <node-id> --prompt "补充异常处理逻辑"
```

`refine` 针对单个 L3 节点进行精化,产物版本号自增并触发棘轮门控校验。

### 校验产物

```bash
goal-studio verify
```

`verify` 运行双评审仲裁(规则评审 + LLM 评审),输出质量分数与不通过项清单。

### 构建最终产物

```bash
goal-studio build
```

`build` 调用对应适配器将 DAG 节点打包为最终产物(`skill` / `code` / `doc` / `workflow`)。

::: tip 命令流水线
典型工作流为:`init` → `draft` → `view` → `refine` → `verify` → `build`。每一步的产物均会被版本化记录,支持回溯。
:::

## Web 界面启动

goal-studio 提供基于 Vite + FastAPI 的可视化编辑界面,适合交互式编排 DAG。

### 启动后端

```bash
uvicorn goal_studio_server.app:create_app --factory --reload --port 8000
```

### 启动前端

在另一个终端执行:

```bash
cd packages/goal-studio-web
pnpm install
pnpm run dev
```

默认访问地址为 `http://localhost:5173`,前端会代理 API 请求到 `http://localhost:8000`。

::: warning 端口冲突
若 8000 或 5173 端口被占用,可通过 `--port` 与 `vite.dev.config.ts` 中的 `server.port` 字段调整。
:::

## 配置 LLM

goal-studio 默认使用 Mock 模式,返回固定桩数据,便于离线体验。要接入真实大模型,需通过环境变量配置。

### OpenAI

```bash
export OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选,默认为官方地址
export GOAL_STUDIO_LLM_MODEL="gpt-4o-mini"          # 可选,默认 gpt-4o-mini
```

### 切换 Provider

通过 `GOAL_STUDIO_LLM_PROVIDER` 切换后端:

```bash
export GOAL_STUDIO_LLM_PROVIDER="mock"   # mock | openai | anthropic
```

::: tip Mock 模式
未设置 `OPENAI_API_KEY` 时,系统自动回退到 Mock 模式并在日志中输出告警。Mock 模式适合 CI 环境与本地学习。
:::

::: warning 密钥安全
请勿将 API Key 提交到版本库。推荐使用 `.env` 文件配合 `python-dotenv` 加载,并将 `.env` 加入 `.gitignore`。
:::

## 下一步

- 阅读 [核心概念](./concepts) 了解三层 DAG 与 GoalContract 设计
- 阅读 [系统架构](./architecture) 了解模块划分与数据流
- 阅读 [适配器开发](./adapter-dev) 自定义产物类型
