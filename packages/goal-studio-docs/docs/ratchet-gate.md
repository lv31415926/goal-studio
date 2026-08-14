# 棘轮门控机制

RatchetGate 是 goal-studio 创作循环中的质量守护组件,确保产物质量**只升不降**,防止 refine 过程中分数回归。

## 什么是棘轮门控

棘轮门控(Ratchet Gate)借鉴机械棘轮"只能单向转动"的思想:在每次 refine 之后,只有当新版本的分数**严格高于**旧版本时,修改才会被保留;否则回滚到修改前的快照。

核心原则:

- **分数只升不降**:`new_score > old_score + threshold` 才保留修改
- **防止回归**:分数下降时自动回滚到 refine 前的图状态
- **历史最佳追踪**:始终记录 `best_score` 与 `best_graph`,最终 build 使用历史最佳版本
- **早停保护**:连续 2 轮提升幅度过小(Δ < `early_stop_delta`)时提前终止,避免无效迭代

::: tip 为什么需要棘轮门控
LLM 驱动的 refine 不一定每次都让产物变好。没有门控时,一次"跑偏"的精化可能毁掉前面多轮积累的质量。棘轮门控把"质量单调递增"作为硬约束,保证创作循环不会越改越差。
:::

## RatchetGate 决策

`RatchetGate.judge()` 根据分数变化返回以下决策之一(定义于 `RatchetDecision` 枚举):

| 决策 | 枚举值 | 触发条件 | 行为 |
| --- | --- | --- | --- |
| **KEEP** | `keep` | `new_score > old_score + threshold` | 保留新图,若 `new_score > best_score` 则更新历史最佳 |
| **REVERT** | `revert` | `new_score <= old_score + threshold` | 回滚到 refine 前的快照,`current_score` 恢复为 `old_score` |
| **EARLY_STOP** | `early_stop` | 连续 2 轮 `\|Δ\| < early_stop_delta` | 终止 Loop,恢复到历史最佳版本 |
| **ARBITRATE** | `arbitrate` | 双评审分差 > `conflict_threshold` | 暂不判定,需调用 `arbitrate()` 进行第三评审 |

::: warning 决策执行顺序
`judge()` 内部会**先检测早停**,再做棘轮判定。即连续 2 轮提升微弱时,即使本轮分数有所上升,也会直接返回 `EARLY_STOP` 而非 `KEEP`。
:::

### 关键参数

`RatchetGate` 是一个 dataclass,可在构造时调整参数:

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `threshold` | `float` | `0.0` | KEEP 阈值,`new > old + threshold` 才保留 |
| `early_stop_delta` | `float` | `2.0` | 连续 2 轮 Δ 小于此值则早停 |
| `conflict_threshold` | `float` | `15.0` | 双评审分差超过此值触发仲裁 |
| `max_snapshots` | `int` | `20` | 快照栈最大容量(LIFO,超出修剪旧快照) |

## 双评审仲裁

为保证评估的客观性,RatchetGate 支持双独立评审模式(`judge_with_peers`)。核心原则是**评技能的 AI 必须与改技能的 AI 不同**。

### 评审流程

1. 准备 2 个独立评估器(实现 `Evaluator` 协议,接受 `NodeGraph` 返回 `Report`)
2. 两个评估器分别对同一张图评分
3. 比较两个分数的分差:
   - **分差 ≤ `conflict_threshold`**(默认 15 分):取两个分数的平均值,交给 `judge()` 做棘轮判定
   - **分差 > `conflict_threshold`**:返回 `ARBITRATE`,需调用 `arbitrate()` 引入第三评审

### 第三评审

当双评审冲突时,调用 `arbitrate()` 引入第三个评估器:

- 第三评审与之前两份报告合并
- 取三个分数的**中位数**作为最终分数
- 再由 `judge()` 做棘轮判定

::: tip 评估器协议
`Evaluator` 是一个 `Protocol`,任何 `Callable[[NodeGraph], Report]` 都可作为评估器。这使得评估器可以是规则引擎、LLM 评审、人工评分或混合策略。
:::

### 报告合并

多评估器报告通过 `_merge_reports()` 合并:

- **分数**:取平均(双评审)或中位数(三评审)
- **维度分数**:各维度分别取平均
- **issues / suggestions**:全部累积
- **passed**:多数表决(超过半数评估器通过则视为通过)

## best_graph 持久化

RatchetGate 在内存中维护 `best_score` 与 `best_graph`,代表历史最高分的图版本。最终 `build` 阶段会**优先使用 `best_graph`** 而非当前图,保证产物质量是历史最高分版本。

### 版本快照栈

每次 refine 前,通过 `save_snapshot()` 保存一个 `GraphSnapshot`:

| 字段 | 说明 |
| --- | --- |
| `version` | 快照版本号(递增) |
| `graph` | refine 前的 NodeGraph |
| `score` | refine 前的分数 |
| `patch_summary` | 触发此次 refine 的 patch 摘要 |
| `timestamp` | 快照时间(ISO 格式) |

快照栈为 LIFO 结构,最大容量 `max_snapshots`(默认 20),超出时修剪最旧的快照。`REVERT` 决策会从栈顶取快照恢复;`undo()` 弹出栈顶实现撤销。

### ArtifactStore 版本管理

最终产物通过 `ArtifactStore` 持久化,默认实现为 `LocalFileArtifactStore`。目录结构:

```
{root}/{artifact_id}/
  ├── manifest.json        # 版本索引
  ├── 0.1.0/
  │   ├── artifact.json    # 完整产物(含 graph_snapshot)
  │   └── files/           # 多文件产物
  └── 0.2.0/
```

- `artifact_id` 由 `product_type` + 内容哈希生成(如 `skill-2bd3993641ad`)
- 每次 `save()` 在 manifest 中追加版本记录
- `list_versions()` 可查询历史版本,`load(artifact_id, version)` 可回溯任意版本

## 验证流程

创作循环中,refine 与 validate 都会触发棘轮门控。完整流程:

```
adapter.validate(graph)        →  Report (含 score)
        │
        ▼
RatchetGate.judge(old, new)    →  RatchetDecision
        │
        ├── KEEP     → 保留 new_graph,更新 best_score / best_graph
        ├── REVERT   → 恢复快照,current_score 回退
        ├── EARLY_STOP → 恢复 best_graph,终止循环
        └── ARBITRATE → 调用 arbitrate() 第三评审
```

### refine 阶段(自动门控)

`POST /api/sessions/{id}/refine` 会自动执行完整的门控流程:

1. **保存快照**:`save_snapshot()` 保存 refine 前的 graph + score
2. **refine**:`adapter.refine(graph, patch)` 生成 new_graph
3. **validate**:`adapter.validate(new_graph)` 得到 new_score
4. **判定**:`ratchet.judge(old_score, new_score, new_graph)` 返回决策
5. **应用决策**:`apply_ratchet_decision()` 更新 graph / best_score / best_graph

### validate 阶段(手动验证)

`POST /api/sessions/{id}/validate` 单独触发验证,更新 `current_score`,并在分数更高时同步更新 `best_score` 与 `best_graph`。

## 使用示例

### 基本棘轮判定

```python
from goal_studio.core.ratchet_gate import RatchetGate, RatchetDecision

gate = RatchetGate(threshold=0.0, early_stop_delta=2.0)

# 第 1 轮:分数提升 → KEEP
decision = gate.judge(old_score=60.0, new_score=75.0, current_graph=graph_v2)
assert decision == RatchetDecision.KEEP
print(f"best_score = {gate.best_score}")  # 75.0

# 第 2 轮:分数下降 → REVERT
decision = gate.judge(old_score=75.0, new_score=68.0, current_graph=graph_v3)
assert decision == RatchetDecision.REVERT
# current_score 已回退为 75.0,best_score 仍为 75.0
```

### 双评审仲裁

```python
from goal_studio.core.ratchet_gate import RatchetGate, RatchetDecision

gate = RatchetGate(conflict_threshold=15.0)

# 两个独立评估器(评技能的 AI ≠ 改技能的 AI)
evaluator_a = MyLLMEvaluator(model="evaluator-llm-a")
evaluator_b = MyRuleEvaluator()

decision, report = gate.judge_with_peers(
    graph=new_graph,
    evaluators=[evaluator_a, evaluator_b],
    old_score=72.0,
    patch_summary="update_node l3-xxx description=...",
)

if decision == RatchetDecision.ARBITRATE:
    # 双评审分差过大,引入第三评审
    decision, report = gate.arbitrate(
        graph=new_graph,
        third_evaluator=MyHumanEvaluator(),
        previous_reports=[report_a, report_b],
        old_score=72.0,
        patch_summary="update_node l3-xxx description=...",
    )
```

### 快照与撤销

```python
gate = RatchetGate(max_snapshots=20)

# refine 前保存快照
gate.save_snapshot(graph=current_graph, score=80.0, patch_summary="add_node ...")

# 撤销最近一次 refine(弹出栈顶快照)
snapshot = gate.undo()
if snapshot:
    restored_graph = snapshot.graph
    restored_score = snapshot.score

# 跳转到历史最佳版本
best_graph = gate.restore_best()
if best_graph:
    print(f"恢复到最佳版本,分数 = {gate.best_score}")
```

### 与 Session 集成

```python
from goal_studio.core.session_manager import SessionManager, SessionPhase

sm = SessionManager("./sessions")
session = sm.create(name="my-skill", goal=goal_contract)

# refine 前自动保存快照
session.save_snapshot(patch_summary="update_node l2-001 slots={...}")

# 应用棘轮决策
final_graph = session.apply_ratchet_decision(
    decision=RatchetDecision.KEEP,
    new_graph=new_graph,
    new_score=88.0,
)

# build 阶段使用 best_graph(历史最佳)
build_graph = session.ratchet.best_graph or session.graph
artifact = adapter.build(build_graph)
sm.save(session)
```

### 查询棘轮历史

```python
# 序列化棘轮状态(不含 graph 对象,只含摘要)
state = gate.to_dict()
# {
#   "threshold": 0.0,
#   "early_stop_delta": 2.0,
#   "conflict_threshold": 15.0,
#   "best_score": 88.0,
#   "current_score": 85.0,
#   "history": [{"round": 1, "old_score": 60, "new_score": 75, ...}, ...],
#   "snapshots": [{"version": 1, "score": 60, "patch_summary": "...", ...}, ...]
# }

# REST API 查询
# GET /api/sessions/{id}/ratchet/history
```

::: tip 相关文档
- [CLI 命令](./cli.md) — `goal-studio verify` 命令触发棘轮判定
- [API 参考](./api.md) — `POST /api/sessions/{id}/refine` 自动门控流程
- [适配器开发](./adapter-dev.md) — 在适配器中实现 `validate()` 返回 Report
:::
