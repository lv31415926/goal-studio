# CLI 命令

`goal-studio` CLI 是命令行入口,覆盖从初始化到构建产物的完整创作循环。所有命令基于 [Click](https://click.palletsprojects.com/) 框架,输出使用 [Rich](https://rich.readthedocs.io/) 美化。

## 命令总览

| 命令 | 说明 |
| --- | --- |
| [`init`](#goal-studio-init) | 初始化项目 |
| [`draft`](#goal-studio-draft) | 起草三层 DAG |
| [`view`](#goal-studio-view) | 可视化呈现当前 DAG |
| [`refine`](#goal-studio-refine) | 精化指定节点 |
| [`verify`](#goal-studio-verify) | 三重门控验证 |
| [`build`](#goal-studio-build) | 固化产物 |
| [`serve`](#goal-studio-serve) | 启动 Web 服务 |
| [`adapters`](#goal-studio-adapters) | 列出已注册适配器 |
| [`scaffold`](#goal-studio-scaffold) | 生成适配器脚手架(Plugin SDK) |
| [`load-plugins`](#goal-studio-load-plugins) | 热加载适配器(Plugin SDK) |
| [`sessions`](#goal-studio-sessions) | 列出所有会话 |
| [`install`](#goal-studio-install) | 安装产物到 maxfun |
| [`import-skill`](#goal-studio-import-skill) | 从 maxfun 导入 skill |
| [`list-skills`](#goal-studio-list-skills) | 列出 maxfun 已有 skills |

::: tip 全局选项
`goal-studio --version` 查看版本号。多数命令支持 `--session-dir` 指定会话存储目录(默认 `./sessions`)。
:::

## goal-studio init

初始化一个 goal-studio 项目,创建标准目录结构与配置文件。

### 用法

```bash
goal-studio init [--name <名称>] [--dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | `str` | `my-project` | 项目名称 |
| `--dir` | `str` | `.` | 项目所在目录 |

### 示例

```bash
# 在当前目录创建 my-project 项目
goal-studio init --name my-project --dir .

# 创建后目录结构
# my-project/
#   ├── sessions/      # 会话存储
#   ├── artifacts/     # 产物输出
#   └── goal.yaml      # 项目配置
```

## goal-studio draft

根据目标契约起草三层 DAG(L1 任务规划 / L2 Agent 分配 / L3 节点精化)。

### 用法

```bash
goal-studio draft --goal <目标> [--type <产物类型>] [--constraint <约束>...] [--criteria <标准>...] [--session-dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--goal` | `str` | **必填** | 目标描述(自然语言) |
| `--type` | `choice` | `skill` | 产物类型:`skill` / `code` / `doc` / `workflow` / `custom` |
| `--constraint` | `str`(可多次) | 无 | 约束条件,可多次传入 |
| `--criteria` | `str`(可多次) | 无 | 成功标准,可多次传入 |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |

### 示例

```bash
# 起草一个技能产物
goal-studio draft \
  --goal "创建一个每日天气查询技能" \
  --type skill \
  --constraint "必须用中文输出" \
  --constraint "不能依赖付费 API" \
  --criteria "能查询指定城市天气" \
  --criteria "响应时间 < 2 秒"
```

::: warning 可重复参数
`--constraint` 和 `--criteria` 支持**多次传入**,每次追加一条。例如 `--constraint "A" --constraint "B"` 会生成约束列表 `["A", "B"]`。
:::

## goal-studio view

可视化呈现当前会话的 DAG,支持文本表格与 JSON 两种格式。

### 用法

```bash
goal-studio view [--format text|json] [--session-dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--format` | `choice` | `text` | 输出格式:`text`(Rich 表格)或 `json` |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |

### 示例

```bash
# 表格形式查看最新会话的 DAG
goal-studio view --format text

# JSON 形式(便于程序处理)
goal-studio view --format json
```

输出包含三层节点的 Layer / ID / Title / Status / Description 信息。`view` 始终读取最新会话(按更新时间排序)。

## goal-studio refine

对指定节点应用精化补丁,生成新版本的 DAG。

### 用法

```bash
goal-studio refine <node_id> --patch <补丁> [--session-dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `node_id` | `str`(参数) | **必填** | 目标节点 ID(如 `l1-001`) |
| `--patch` | `str` | **必填** | 精化补丁(JSON 或自然语言描述) |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |

### 示例

```bash
# 用自然语言描述精化节点
goal-studio refine l2-001 --patch "把执行 Agent 改成支持并行的 batch 模式"

# 用 JSON 补丁
goal-studio refine l3-002 --patch '{"op": "update_node", "slots": {"description": "新增重试逻辑"}}'
```

::: tip MVP 行为
当前 CLI 的 `refine` 将 patch 字符串作为 `description` 槽位更新。完整的 IntentParser LLM 解析在 Phase 2 接入,届时 patch 可为任意自然语言指令。
:::

## goal-studio verify

对当前会话执行三重门控验证:调用适配器 `validate()` 评估,再由 RatchetGate 做棘轮判定。

### 用法

```bash
goal-studio verify [--session-dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |

### 示例

```bash
goal-studio verify
```

输出包含:

- **评估者**与**总分**(PASS / FAIL)
- **各维度分数**(如 structure / effect / safety)
- **问题列表**与**改进建议**
- **棘轮判定结果**(KEEP 保留 / REVERT 回滚 / EARLY_STOP 早停)

::: warning MVP 评估器
当前 `verify` 使用单评估器(适配器内置)。双独立评审 + 仲裁在 Phase 2 接入,详见[棘轮门控机制](./ratchet-gate.md)。
:::

## goal-studio build

将当前会话的 DAG 固化为最终产物,持久化到 ArtifactStore 并导出。

### 用法

```bash
goal-studio build [--session-dir <目录>] [-o <输出目录>] [--target <导出目标>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |
| `-o`, `--output` | `str` | `./artifacts` | 产物输出目录(ArtifactStore 根) |
| `--target` | `str` | `file://./artifacts` | 导出目标,如 `file://./output` / `git://repo` |

### 示例

```bash
# 默认导出到本地 ./artifacts
goal-studio build

# 指定输出目录与导出目标
goal-studio build -o ./my-artifacts --target file://./dist
```

产物通过 `LocalFileArtifactStore` 保存,按 `artifact_id / version` 组织目录结构,并附带 `manifest.json` 版本索引。

## goal-studio serve

::: warning 占位命令
`serve` 当前为占位实现(Phase 2 规划),仅打印提示,不会真正启动服务。请改用下方「实际启动方式」。
:::

### 实际启动方式

Web 服务由 FastAPI 后端 + React 前端组成,分别启动:

```bash
# 1. 后端 (FastAPI, 端口 8000)
uvicorn goal_studio_server.app:create_app --factory --reload --port 8000

# 2. 前端 (Vite dev server, 端口 5173, 自动代理 /api 与 /ws 到 8000)
cd packages/goal-studio-web
pnpm run dev
```

访问 `http://localhost:5173` 即可使用可视化创作界面。生产部署见[生产部署](/deployment)。

## goal-studio adapters

列出所有已注册的产物适配器。

### 用法

```bash
goal-studio adapters
```

### 参数

无参数。

### 示例

```bash
goal-studio adapters
```

输出表格包含 `product_type` / `name` / `description` 三列。内置适配器包括 skill / code / doc / workflow,通过 [Plugin SDK](#goal-studio-scaffold) 可注册自定义适配器。

## goal-studio scaffold

生成适配器脚手架代码(Plugin SDK),并自动验证生成的适配器是否符合 `BaseAdapter` 接口。

### 用法

```bash
goal-studio scaffold --name <适配器名> --type <产物类型> [--desc <描述>] [-o <输出目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | `str` | **必填** | 适配器类名(如 `MyAdapter`) |
| `--type` | `str` | **必填** | 产物类型标识(如 `custom`) |
| `--desc` | `str` | `""` | 一句话描述 |
| `-o`, `--output` | `str` | `./adapters` | 输出目录 |

### 示例

```bash
goal-studio scaffold \
  --name NotionAdapter \
  --type notion \
  --desc "导出 Notion 文档的适配器" \
  -o ./adapters
```

生成后会自动尝试加载并验证:

- 通过验证:输出 `✓ 适配器验证通过`
- 存在问题:列出未实现的抽象方法等

::: tip 后续步骤
脚手架生成后:1) 编辑文件实现 `draft` / `refine` / `validate` / `build` / `export` 五个方法;2) 放入适配器目录(如 `./adapters/`);3) 运行 `goal-studio adapters` 确认加载成功。
:::

## goal-studio load-plugins

从目录热加载适配器(Plugin SDK),无需重启服务。

### 用法

```bash
goal-studio load-plugins [--dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--dir` | `str` | `./adapters` | 适配器目录 |

### 示例

```bash
# 从默认目录加载
goal-studio load-plugins

# 指定目录
goal-studio load-plugins --dir ./my-adapters
```

加载成功后输出适配器类名列表;目录为空或无有效适配器时给出警告。

## goal-studio sessions

列出所有会话及其状态摘要。

### 用法

```bash
goal-studio sessions [--session-dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--session-dir` | `str` | `./sessions` | 会话存储目录 |

### 示例

```bash
goal-studio sessions
```

输出表格包含:

| 列 | 说明 |
| --- | --- |
| ID | 会话唯一标识(12 位 hex) |
| 名称 | 会话名称 |
| 阶段 | `init` / `draft` / `visualize` / `refine` / `verify` / `build` / `done` |
| 最佳分数 | 历史最高分(棘轮门控记录) |
| 更新时间 | 最后更新时间 |

## goal-studio install

将指定会话的构建产物安装到运行环境（maxfun 或文件系统）。需要先执行 `build` 生成产物。

### 用法

```bash
goal-studio install --session <会话ID> [--target maxfun|file] [--skill-dir <目录>] [--name <名称>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-s`, `--session` | `str` | **必填** | 会话 ID |
| `--target` | `str` | `maxfun` | 安装目标（`maxfun` 或 `file`） |
| `--skill-dir` | `str` | 自动探测 | maxfun skill 目录（默认自动探测） |
| `--name` | `str` | 从产物提取 | skill 名称 |

### 示例

```bash
# 安装到 maxfun（自动探测目录）
goal-studio install -s a1b2c3d4e5f6

# 安装到指定目录
goal-studio install -s a1b2c3d4e5f6 --skill-dir ~/.maxfun/skills

# 安装为文件
goal-studio install -s a1b2c3d4e5f6 --target file
```

安装成功后输出安装路径、skill 名称与文件数量。maxfun 目录探测优先级：环境变量 `MAXFUN_SKILL_DIR` → `~/.maxfun/skills` → `~/.trae-cn/skills` → `./skills`。

## goal-studio import-skill

从 maxfun 目录导入已有 skill，生成 goal-studio DAG 结构提示。

### 用法

```bash
goal-studio import-skill --skill-dir <目录> [--name <名称>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--skill-dir` | `str` | **必填** | maxfun skill 目录路径 |
| `--name` | `str` | 从目录提取 | skill 名称 |

### 示例

```bash
# 导入 maxfun skill
goal-studio import-skill --skill-dir ~/.maxfun/skills/my-skill
```

输出包含 skill 元信息（名称、描述、版本、触发词、文件数）和建议的 DAG 结构（L1 节点列表），可直接用于 `draft` 命令作为初始 DAG 参考。

## goal-studio list-skills

列出 maxfun 目录中已有的 skills。

### 用法

```bash
goal-studio list-skills [--dir <目录>]
```

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--dir` | `str` | 自动探测 | maxfun skill 目录 |

### 示例

```bash
# 列出自动探测目录的 skills
goal-studio list-skills

# 指定目录
goal-studio list-skills --dir ~/.maxfun/skills
```

输出表格包含名称、版本、描述、触发词、文件数五列。目录为空时给出提示。

::: tip 相关文档
- [核心概念](./concepts.md) — 三层 DAG 与创作循环
- [棘轮门控机制](./ratchet-gate.md) — 分数只升不降的质量守护
- [适配器开发](./adapter-dev.md) — 通过 Plugin SDK 扩展产物类型
:::
