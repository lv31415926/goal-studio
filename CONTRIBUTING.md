# 贡献指南

感谢你对 goal-studio 的关注！本文档指导你如何参与贡献。

## 开发环境

```bash
# 后端
cd packages/goal-studio-core
pip install -e ".[dev]"
pytest

# 前端
cd packages/goal-studio-web
pnpm install
pnpm dev
```

## 开发新适配器

适配器是 goal-studio 的核心扩展点。实现 `BaseAdapter` 的 6 个方法即可支持新的产物类型：

```python
from goal_studio.core import BaseAdapter, GoalContract, NodeGraph, Patch, Report, Artifact

class MyAdapter(BaseAdapter):
    def draft(self, goal: GoalContract) -> NodeGraph: ...
    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph: ...
    def validate(self, graph: NodeGraph) -> Report: ...
    def build(self, graph: NodeGraph) -> Artifact: ...
    def export(self, artifact: Artifact, target: str) -> str: ...
```

详见 [packages/goal-studio-core/src/goal_studio/core/base_adapter.py](packages/goal-studio-core/src/goal_studio/core/base_adapter.py)。

## 提交规范

- feat: 新功能
- fix: 修复
- refactor: 重构
- docs: 文档
- test: 测试
- chore: 杂项

## 分支策略

- main: 稳定发布
- develop: 开发集成
- feature/*: 功能分支
- fix/*: 修复分支

## Issue

欢迎提交：
- Bug 报告
- 新适配器建议
- 性能问题
- 文档改进
