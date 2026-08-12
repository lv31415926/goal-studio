<div align="center">

# goal-studio

**可视化 Loop 创作系统** — 把目标变成可复用产物的可视化创作循环

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![TypeScript 5](https://img.shields.io/badge/TypeScript-5-blue.svg)](https://typescriptlang.org)

</div>

> **为什么是 goal-studio？** Langflow / Dify / n8n / Flowise / Coze 五大可视化 AI 编排平台的"开发者编辑画布"环节都没有语音输入。goal-studio 把"起草-呈现-精化-验证"的棘轮循环与多模态（语音/文本/鼠标）节点编辑结合，让创作过程可视化、可对话、可回滚、可复用。

## 核心特性

- **Loop-First 创作循环** — Draft → Visualize → Refine → Verify 四阶段，每轮棘轮门控，分数只能升不能降
- **多模态节点编辑** — 语音说"把这个改成异步"，三信号融合算法精准定位节点
- **三层可视化画布** — L1 任务规划 / L2 Agent 分配 / L3 节点精化，语义缩放自适应
- **可插拔产物适配器** — 技能 / 代码 / 文档 / 工作流，实现 6 方法接口即可扩展
- **独立开源** — 核心引擎零耦合，可 `pip install` 独立使用，也可作为 maxfun skill 集成

## 快速开始

### 安装

```bash
# Python 核心
pip install goal-studio

# 启动 Web 界面
goal-studio serve
```

### CLI 用法

```bash
# 1. 起草三层 DAG
goal-studio draft --goal "创建一个日报生成技能" --type skill

# 2. 可视化呈现
goal-studio view

# 3. 精化指定节点
goal-studio refine node-3 --patch "把执行方式改成异步"

# 4. 三重门控验证
goal-studio verify

# 5. 固化为产物
goal-studio build --output ./my-skill
```

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  多模态输入层 (语音/文本/鼠标) → 三信号融合              │
├─────────────────────────────────────────────────────────┤
│  IntentParser 五层架构                                  │
│  采集 → 对齐 → LLM解析 → 节点引用消解 → 动作执行         │
├─────────────────────────────────────────────────────────┤
│  核心引擎 (goal-studio-core)                            │
│  NodeGraph · AdapterRegistry · RatchetGate              │
│  KnowledgeStore · ArtifactStore · SessionManager        │
├─────────────────────────────────────────────────────────┤
│  三层画布 (React Flow)  ←→  产物适配器 (可插拔)         │
│  L1规划 / L2分配 / L3精化    Skill/Code/Doc/Workflow    │
├─────────────────────────────────────────────────────────┤
│  产物输出: Artifact 持久化 · Git/文件/可扩展            │
└─────────────────────────────────────────────────────────┘
```

## 创作循环

```
   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
   │  Draft   │ →  │Visualize │ →  │  Refine  │ →  │  Verify  │
   │ AI起草   │    │ 画布呈现 │    │ 多模态   │    │ 棘轮门控 │
   └──────────┘    └──────────┘    └──────────┘    └────┬─────┘
        ↑                                              │
        └──────────── 分数↑ → KEEP ───────────────────┤
                       分数↓ → REVERT ↺ ──────────────┘
```

## 适配器开发

实现 `BaseAdapter` 的 6 个方法即可支持新的产物类型：

```python
from goal_studio.core import BaseAdapter, GoalContract, NodeGraph, Patch, Report, Artifact

class MyAdapter(BaseAdapter):
    def draft(self, goal: GoalContract) -> NodeGraph:
        """目标 → 初始三层 DAG"""

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        """应用用户精化补丁"""

    def validate(self, graph: NodeGraph) -> Report:
        """产物特定验证"""

    def build(self, graph: NodeGraph) -> Artifact:
        """DAG → 最终产物"""

    def export(self, artifact: Artifact, target: str) -> str:
        """导出到 file/git/..."""
```

详见 [packages/goal-studio-core/src/goal_studio/core/base_adapter.py](packages/goal-studio-core/src/goal_studio/core/base_adapter.py)。

## 项目结构

```
goal-studio/
├── packages/
│   ├── goal-studio-core/      # Python 核心引擎 (pip install)
│   ├── goal-studio-web/       # React 前端 (npm install)
│   └── goal-studio-docs/      # VitePress 文档
├── examples/                  # 示例项目
├── plugins/                   # 社区适配器 (maxfun-adapter 等)
└── .github/workflows/         # CI
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+, Pydantic v2, Click, asyncio |
| 前端 | React 18, TypeScript 5, @xyflow/react, Zustand+Jotai, ELK.js, Tailwind v4 |
| 多模态 | Vercel AI SDK 7, Web Speech API |
| 文档 | VitePress |

## 理论基础

- **DesignerlyLoop** (Cross) — 设计学循环理论
- **SkillOpt** (Microsoft) — 技能优化的 9 维评估体系
- **ParsingDST** (Wu et al.) — LLM 对话状态跟踪
- **POLAR** (Yonsei+KAIST) — 个性化实例接地

## 贡献

欢迎提交 Issue 和 PR！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Apache 2.0 © 2026 goal-studio contributors
