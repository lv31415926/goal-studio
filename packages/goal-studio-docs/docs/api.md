# API 参考

goal-studio 提供三层 API:REST API(同步请求/响应)、WebSocket(实时双向通信)、Python API(库直接调用)。

## REST API

REST API 基于 FastAPI 实现,前缀 `/api`。所有会话相关操作通过 `/api/sessions` 路由访问。

::: tip 在线文档
服务启动后访问 `http://localhost:8000/docs` 可查看交互式 API 文档(Swagger UI)。
:::

### POST /api/sessions

创建新的创作会话。

**请求体**(`CreateSessionRequest`):

```json
{
  "name": "my-skill-project",
  "goal": {
    "goal": "创建一个每日天气查询技能",
    "prerequisites": ["已有 OpenWeather API Key"],
    "constraints": ["必须用中文输出", "不能依赖付费 API"],
    "success_criteria": ["能查询指定城市天气", "响应时间 < 2 秒"],
    "product_type": "skill",
    "metadata": { "author": "yanlv" }
  }
}
```

::: tip goal 字段可选
创建会话时可不传 `goal`,后续调用 `/draft` 时再传入目标契约。
:::

**响应**(`SessionModel`):

```json
{
  "id": "a1b2c3d4e5f6",
  "name": "my-skill-project",
  "goal": {
    "goal": "创建一个每日天气查询技能",
    "prerequisites": ["已有 OpenWeather API Key"],
    "constraints": ["必须用中文输出", "不能依赖付费 API"],
    "success_criteria": ["能查询指定城市天气", "响应时间 < 2 秒"],
    "product_type": "skill",
    "metadata": { "author": "yanlv" }
  },
  "graph": null,
  "phase": "init",
  "best_score": 0.0,
  "current_score": 0.0,
  "ratchet_history": [],
  "dialog": [],
  "created_at": "2026-08-13T10:00:00",
  "updated_at": "2026-08-13T10:00:00",
  "metadata": {}
}
```

### POST /api/sessions/{id}/draft

起草三层 DAG,调用 `Adapter.draft()` 生成初始图。

**请求体**(`DraftRequest`):

```json
{
  "goal": {
    "goal": "创建一个每日天气查询技能",
    "prerequisites": ["已有 OpenWeather API Key"],
    "constraints": ["必须用中文输出"],
    "success_criteria": ["能查询指定城市天气"],
    "product_type": "skill",
    "metadata": {}
  }
}
```

**响应**(`NodeGraphModel`):

```json
{
  "nodes": {
    "l1-001": {
      "id": "l1-001",
      "layer": 1,
      "title": "需求分析",
      "description": "解析天气查询需求",
      "node_type": "task",
      "status": "draft",
      "slots": {},
      "parent_id": null,
      "children_ids": ["l2-001"],
      "position": [0.0, 0.0],
      "metadata": {}
    }
  },
  "edges": [
    {
      "id": "e1",
      "source": "l1-001",
      "target": "l1-002",
      "label": "",
      "edge_type": "data",
      "metadata": {}
    }
  ],
  "root_id": "l1-001",
  "version": 1
}
```

### POST /api/sessions/{id}/refine

精化补丁 — 自动执行 refine → validate → 棘轮门控完整流程。

**请求体**(`RefineRequest`):

```json
{
  "patch": {
    "op": "update_node",
    "target_node_id": "l2-001",
    "target_node_id_2": null,
    "slots": { "description": "改为支持并行的 batch 模式" },
    "new_node": null,
    "position": null,
    "source": "text",
    "rationale": "用户要求提升吞吐"
  }
}
```

**PatchOp 枚举值**:`add_node` / `update_node` / `remove_node` / `add_edge` / `remove_edge` / `reorder`

**响应**(`RatchetResponse`):

```json
{
  "decision": "keep",
  "old_score": 72.0,
  "new_score": 85.5,
  "delta": 13.5,
  "best_score": 85.5,
  "current_score": 85.5,
  "reverted": false,
  "early_stopped": false,
  "message": "保留修改 (分数 72.0 → 85.5)",
  "graph": {
    "nodes": { "...": "更新后的节点" },
    "edges": [],
    "root_id": "l1-001",
    "version": 2
  }
}
```

::: warning 决策类型
`decision` 可能值为:`keep`(保留) / `revert`(回滚) / `early_stop`(早停) / `arbitrate`(需仲裁)。当 `reverted=true` 时,响应中的 `graph` 为回滚后的快照图。详见[棘轮门控机制](./ratchet-gate.md)。
:::

### POST /api/sessions/{id}/validate

触发验证 — 调用 `Adapter.validate()` 评估当前图,更新 `current_score` 与 `best_score`。

**请求体**:无。

**响应**(`ReportModel`):

```json
{
  "passed": true,
  "score": 85.5,
  "dimensions": {
    "structure": 90.0,
    "effect": 80.0,
    "safety": 86.5
  },
  "issues": ["L3 节点 l3-002 缺少 description"],
  "suggestions": ["为 l3-002 补充槽位说明"],
  "evaluator": "auto"
}
```

### POST /api/sessions/{id}/build

构建最终产物。**使用 `best_graph`(历史最佳)而非当前图**,保证产物质量是历史最高分版本。

**请求体**:无。

**响应**(`ArtifactModel`):

```json
{
  "product_type": "skill",
  "content": "# 每日天气查询技能\n\n## 触发词\n天气查询、查天气...",
  "files": {
    "SKILL.md": "# 每日天气查询技能\n...",
    "handler.py": "def handle(city): ..."
  },
  "metadata": { "node_count": 12, "edge_count": 8 },
  "graph_snapshot": {
    "nodes": { "...": "生成此产物的图快照" },
    "edges": [],
    "root_id": "l1-001",
    "version": 4
  },
  "version": "0.1.0"
}
```

### GET /api/sessions/{id}

获取会话详情(含完整 NodeGraph)。

**响应**(`SessionModel`):同 [POST /api/sessions](#post-api-sessions) 响应,但 `graph` 字段非空。

### GET /api/health

健康检查端点。

**响应**:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### 其他端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/sessions` | 列出所有会话摘要 |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `POST` | `/api/sessions/{id}/ratchet/undo` | 撤销最近一次 refine |
| `POST` | `/api/sessions/{id}/ratchet/restore_best` | 恢复到历史最佳版本 |
| `GET` | `/api/sessions/{id}/ratchet/history` | 获取棘轮门控历史 |
| `GET` | `/api/adapters` | 列出已注册适配器 |

## WebSocket

WebSocket 端点提供实时双向通信,支持多模态输入(文本/语音/鼠标)与增量推送。

### 连接

```
ws://host:port/ws/sessions/{session_id}
```

连接时会验证 `session_id` 存在性,不存在则关闭连接(码 `4404`)。

### 客户端 → 服务端消息

所有消息为 JSON 格式:`{ "type": "<类型>", "payload": { ... } }`

#### intent_parse

解析自然语言指令,触发五层流水线全流程。

```json
{
  "type": "intent_parse",
  "payload": {
    "text": "把第二个节点改成并行模式",
    "is_mobile": false,
    "force": false
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | `str` | 用户输入文本 |
| `is_mobile` | `bool` | 是否移动端(影响模态路由) |
| `force` | `bool` | 确认后强制执行(跳过删除级联确认门控) |

#### voice_chunk

流式 ASR chunk,支持增量解析。

```json
{
  "type": "voice_chunk",
  "payload": {
    "text": "把第二个节点",
    "confidence": 0.92,
    "is_final": false
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `text` | `str` | ASR 转录文本 |
| `confidence` | `float` | ASR 置信度(供消歧参考) |
| `is_final` | `bool` | 是否最终 chunk(interim chunk 会节流处理) |

::: tip interim 节流
interim chunk(`is_final=false`)会节流处理:同一文本短时间内不重复解析,全局间隔 ≥ 0.3 秒,避免每个 interim chunk 都触发 LLM。最终 chunk(`is_final=true`)走完整解析。
:::

#### mouse_select

鼠标选中节点,推入信号缓冲区供消歧使用。

```json
{
  "type": "mouse_select",
  "payload": { "node_id": "l1-002" }
}
```

#### mouse_hover

鼠标悬停节点(传 `null` 表示移开悬停)。

```json
{
  "type": "mouse_hover",
  "payload": { "node_id": "l1-002" }
}
```

### 服务端 → 客户端消息

所有消息附带 `session_id` 与 `timestamp` 字段。

#### intent_parsed

意图解析结果(Layer 3 输出)。

```json
{
  "type": "intent_parsed",
  "payload": {
    "intent": {
      "op": "update_node",
      "target_ref": { "type": "ordinal", "value": "第二个" },
      "slots": { "mode": "parallel" },
      "is_valid": true,
      "ambiguity_flag": false,
      "parse_source": "llm"
    }
  },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.0
}
```

#### node_resolved

节点消解结果(Layer 4 输出)。

```json
{
  "type": "node_resolved",
  "payload": {
    "resolve": {
      "ok": true,
      "node_id": "l1-002",
      "reason": "按序数匹配第 2 个 L1 节点",
      "clarification_options": [],
      "confidence": 0.95
    }
  },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.1
}
```

#### action_executed

动作执行结果(Layer 5 输出),含执行状态与可选的图更新。

```json
{
  "type": "action_executed",
  "payload": {
    "execution": {
      "ok": true,
      "reason": "update_node l1-002 slots={mode: parallel}",
      "undo_available": true,
      "patch": {
        "op": "update_node",
        "target_node_id": "l1-002",
        "target_node_id_2": null,
        "slots": { "mode": "parallel" }
      },
      "needs_confirmation": false,
      "confirmation_context": {},
      "cycle_path": null,
      "conflict_warning": ""
    },
    "graph": {
      "nodes": { "...": "更新后的图" },
      "edges": [],
      "root_id": "l1-001",
      "version": 3
    }
  },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.2
}
```

#### graph_update

图更新通知,版本号递增。

```json
{
  "type": "graph_update",
  "payload": { "graph": { "...": "完整 NodeGraph" }, "version": 3 },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.3
}
```

#### phase_change

会话阶段变化通知。

```json
{
  "type": "phase_change",
  "payload": { "phase": "refine" },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.4
}
```

#### error

错误通知。

```json
{
  "type": "error",
  "payload": { "error": "节点引用歧义,请明确指定", "code": "ambiguity" },
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1723536000.5
}
```

常见 `code` 值:`empty_text` / `bad_json` / `bad_type` / `ambiguity` / `pipeline_error` / `pipeline_init` / `server_error`。

### 消息类型汇总

| 方向 | 类型 | 说明 |
| --- | --- | --- |
| 客户端 → 服务端 | `intent_parse` | 解析自然语言指令 |
| 客户端 → 服务端 | `voice_chunk` | 流式 ASR chunk |
| 客户端 → 服务端 | `mouse_select` | 鼠标选中节点 |
| 客户端 → 服务端 | `mouse_hover` | 鼠标悬停节点 |
| 客户端 → 服务端 | `ping` | 心跳 |
| 服务端 → 客户端 | `intent_parsed` | 意图解析结果 |
| 服务端 → 客户端 | `node_resolved` | 节点消解结果 |
| 服务端 → 客户端 | `action_executed` | 动作执行结果 |
| 服务端 → 客户端 | `graph_update` | 图更新 |
| 服务端 → 客户端 | `phase_change` | 阶段变化 |
| 服务端 → 客户端 | `error` | 错误 |
| 服务端 → 客户端 | `pong` | 心跳响应 |

## Python API

goal-studio 的核心能力以 Python 库形式提供,可直接在代码中调用。

### GoalContract

用户目标契约,创作的起点。包含四要素 + 产物类型。

```python
from goal_studio.core.contract import GoalContract, ProductType

goal = GoalContract(
    goal="创建一个每日天气查询技能",
    prerequisites=["已有 OpenWeather API Key"],
    constraints=["必须用中文输出", "不能依赖付费 API"],
    success_criteria=["能查询指定城市天气", "响应时间 < 2 秒"],
    product_type=ProductType.SKILL,
    metadata={"author": "yanlv"},
)

# 校验契约完整性(返回问题列表,空=通过)
issues = goal.validate()
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `goal` | `str` | 用户目标(自然语言) |
| `prerequisites` | `list[str]` | 先决条件(已有资源/上下文) |
| `constraints` | `list[str]` | 约束条件(必须满足的限制) |
| `success_criteria` | `list[str]` | 成功标准(可验证的验收条件) |
| `product_type` | `ProductType` | 产物类型:`SKILL` / `CODE` / `DOC` / `WORKFLOW` / `CUSTOM` |
| `metadata` | `dict` | 额外元信息 |

### NodeGraph

三层 DAG 数据结构,支持拓扑校验与序列化。

```python
from goal_studio.core.node_graph import (
    NodeGraph, Node, Edge, NodeLayer, NodeStatus,
)

graph = NodeGraph(root_id="l1-001")

# 添加节点
graph.add_node(Node(
    id="l1-001",
    layer=NodeLayer.L1_PLANNING,
    title="需求分析",
    description="解析天气查询需求",
))
graph.add_node(Node(
    id="l2-001",
    layer=NodeLayer.L2_ALLOCATION,
    title="执行 Agent",
    parent_id="l1-001",
))

# 添加边(自动拓扑校验,拒绝成环)
graph.add_edge(Edge(id="e1", source="l1-001", target="l1-002"))

# 拓扑排序
order = graph.topological_order(layer=NodeLayer.L1_PLANNING)

# 序列化 / 反序列化
data = graph.to_dict()
restored = NodeGraph.from_dict(data)
```

| 方法 | 说明 |
| --- | --- |
| `add_node(node)` | 添加节点(自动维护父子关系) |
| `remove_node(id, cascade=True)` | 移除节点(级联移除子节点) |
| `add_edge(edge)` | 添加边(自动检测成环) |
| `topological_order(layer=None)` | 拓扑排序(可按层过滤) |
| `get_layer(layer)` | 获取指定层所有节点 |
| `get_descendants(node_id)` | 获取所有后代(级联删除范围) |
| `to_dict()` / `from_dict()` | 序列化 / 反序列化 |

三层结构:`L1_PLANNING`(任务规划) / `L2_ALLOCATION`(Agent 分配) / `L3_REFINEMENT`(节点精化)。

### BaseAdapter

产物适配器抽象基类,核心扩展点。每种产物类型实现一个 Adapter。

```python
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import NodeGraph

class MyAdapter(BaseAdapter):
    product_type = "custom"
    name = "my-adapter"
    description = "自定义适配器"

    def draft(self, goal: GoalContract) -> NodeGraph:
        ...

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        ...

    def validate(self, graph: NodeGraph) -> Report:
        ...

    def build(self, graph: NodeGraph) -> Artifact:
        ...

    def export(self, artifact: Artifact, target: str) -> str:
        ...
```

| 方法 | 必填 | 说明 |
| --- | --- | --- |
| `draft(goal)` | 是 | 目标 → 初始 DAG |
| `refine(graph, patch)` | 是 | 应用精化补丁(不可变语义) |
| `validate(graph)` | 是 | 产物验证,返回 `Report`(含 score) |
| `build(graph)` | 是 | DAG → 最终产物 `Artifact` |
| `export(artifact, target)` | 是 | 导出(`file://` / `git://` / `feishu://`) |
| `install(artifact, env)` | 否 | 安装到运行环境(默认不支持) |
| `get_schema()` | 否 | 节点槽位 schema(供前端渲染表单) |
| `estimate_complexity(graph)` | 否 | 估算复杂度 1-10(默认按节点数) |

::: tip 不可变语义
`refine()` 应返回**新的 NodeGraph**,不修改传入的 graph。这保证了棘轮门控可以在 KEEP / REVERT 间安全切换。
:::

### IntentPipeline

五层意图解析流水线,整合多模态输入到图操作。

```python
from goal_studio.intent_parser.pipeline import IntentPipeline
from goal_studio.llm.base import BaseLLMProvider

llm = MyLLMProvider()
pipeline = IntentPipeline(graph=current_graph, llm=llm, dialog_history=dialog)

# 接收多模态输入
pipeline.ingest_text("把第二个节点改成并行模式")
pipeline.ingest_voice("把第二个节点改成并行模式", confidence=0.92)
pipeline.ingest_mouse_select("l1-002")
pipeline.ingest_mouse_hover("l1-002")

# 执行五层流水线
result = pipeline.process(text="把第二个节点改成并行模式", is_mobile=False)

# 结果包含各层输出
if result.intent:
    print(f"意图: {result.intent.op}")
if result.resolve:
    print(f"消解节点: {result.resolve.node_id}")
if result.execution and result.execution.ok:
    print(f"执行成功,新图版本: {result.final_graph.version}")
```

| 方法 | 说明 |
| --- | --- |
| `ingest_voice(transcript, confidence)` | 接收语音转录(Layer 1) |
| `ingest_text(text)` | 接收文本输入(Layer 1) |
| `ingest_mouse_select(node_id)` | 接收鼠标选中(Layer 1) |
| `ingest_mouse_hover(node_id)` | 接收鼠标悬停(Layer 1) |
| `process(text, is_mobile, force)` | 执行完整五层流水线 |
| `update_graph(graph)` | 更新当前图 |
| `undo()` / `redo()` | 撤销 / 重做 |

五层结构:`Layer 1 采集` → `Layer 2 对齐` → `Layer 3 LLM解析` → `Layer 4 节点消解` → `Layer 5 动作执行`。

### RatchetGate

棘轮门控,确保分数只升不降。详见[棘轮门控机制](./ratchet-gate.md)。

```python
from goal_studio.core.ratchet_gate import RatchetGate, RatchetDecision

gate = RatchetGate(threshold=0.0, early_stop_delta=2.0, conflict_threshold=15.0)

# refine 前保存快照
gate.save_snapshot(graph=current_graph, score=72.0, patch_summary="update_node l2-001")

# 棘轮判定
decision = gate.judge(old_score=72.0, new_score=85.5, current_graph=new_graph)

# 双评审仲裁
decision, report = gate.judge_with_peers(
    graph=new_graph, evaluators=[eval_a, eval_b], old_score=72.0,
)

# 撤销 / 恢复最佳
snapshot = gate.undo()
best_graph = gate.restore_best()
```

| 方法 | 说明 |
| --- | --- |
| `save_snapshot(graph, score, patch_summary)` | refine 前保存快照 |
| `judge(old_score, new_score, current_graph)` | 棘轮判定(单评估器) |
| `judge_with_peers(graph, evaluators, old_score)` | 双独立评审判定 |
| `arbitrate(graph, third_evaluator, previous_reports, old_score)` | 第三评审仲裁 |
| `undo()` | 撤销最近一次 refine |
| `restore_best()` | 恢复到历史最佳版本 |
| `to_dict()` | 序列化(不含 graph 对象) |

### ArtifactStore

产物持久化与版本管理。

```python
from goal_studio.core.artifact_store import LocalFileArtifactStore

store = LocalFileArtifactStore("./artifacts")

# 保存产物(返回 artifact_id)
artifact_id = store.save(artifact, note="session=a1b2c3d4e5f6")

# 加载产物(默认最新版本)
loaded = store.load(artifact_id)
loaded_v1 = store.load(artifact_id, version="0.1.0")

# 列出所有版本
versions = store.list_versions(artifact_id)
for v in versions:
    print(f"{v.version} - {v.created_at} - {v.note}")

# 删除
store.delete(artifact_id)               # 删除所有版本
store.delete(artifact_id, version="0.1.0")  # 删除指定版本
```

| 方法 | 说明 |
| --- | --- |
| `save(artifact, note)` | 保存产物,返回 `artifact_id` |
| `load(artifact_id, version=None)` | 加载产物(默认最新版本) |
| `list_versions(artifact_id)` | 列出所有版本 |
| `delete(artifact_id, version=None)` | 删除产物或指定版本 |

::: tip 相关文档
- [核心概念](./concepts.md) — GoalContract / NodeGraph / Adapter 的设计理念
- [棘轮门控机制](./ratchet-gate.md) — RatchetGate 完整说明
- [IntentParser 详解](./intent-parser.md) — 五层流水线深入解析
- [CLI 命令](./cli.md) — 命令行操作
:::
