# Changelog

本文件记录 goal-studio 的显著变更。遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-08-15

### 前沿机制升级 P3 批次(DAG 执行引擎 + 棘轮数据飞轮)

#### Added (新增)
- 方案 #1 DAG 执行引擎(grounded validation 落地): `core/execution.py` —
  按拓扑序实际执行 NodeGraph,条件边安全求值(无 eval,支持 ==/!=/>/>=/</<= 与裸键真值),
  把「图看起来对」升级为「图跑得通」;三类接地失败显式中止并给诊断:
  成环 → ABORTED / BLOCKED 节点 → FAILED / 条件键无生产者 → ABORTED;
  节点输出三层发布到上下文(node.id/归一化标题/扁平 slot 键),
  分支未命中侧下游级联 SKIPPED;`BaseAdapter` 新增 `get_executor`/`get_router`
  可选扩展点(默认模拟执行 + ConditionRouter);REST 暴露
  `POST /api/sessions/{id}/execute` 与 `GET /execution/records`,
  执行历史(上限 20 次)持久化进会话 metadata
- 方案 #2 棘轮数据飞轮: `core/ratchet_data.py`(RefineRecord + 25 维特征
  [图结构/补丁 one-hot/差分/分数上下文] + SQLite 线程安全存储,零第三方依赖)
  与 `core/training.py`(PatchClassifier 梯度提升树,sklearn 可选依赖,
  样本 <30 或单类别拒绝训练,输出 accuracy/precision/recall/f1 + 特征重要性 Top-10);
  `/refine` 每次棘轮判定自动沉淀样本(best-effort 不阻断主流程);
  REST 暴露 `GET /api/flywheel/stats`、`POST /train`、`POST /predict`
  (预测为咨询式参考,不参与棘轮门控,避免小样本模型错误拦截用户修改)

#### Fixed (修复)
- F1: CLI `build` 改用 `ratchet.best_graph`(深拷贝)固化产物 — 此前用
  `session.graph`,refine 后未经验证的图会直接进产物,违反「build 必须用
  best_graph」硬约束且与 server `/build` 行为分叉
- F2: CLI `verify` PASS 时沉淀 Voyager 技能(pattern 条目与 server
  `_store_pattern` 同形状,server 检索端可直接命中)— 此前纯 CLI 用户
  `goal-studio skills` 恒为空
- F5: `scaffold` 同步生成 `.signature` 伴随文件 — PR-O 签名校验导致脚手架
  产物天生无法被 `load-plugins` 加载(断链);adapter-dev.md 补充
  目录白名单 + 签名标记的安全约束文档
- F6: mock workflow 图 `l1-branch` 节点补「校验通过」信号 slot — 条件边
  引用的键此前无任何节点发布,演示图 `/execute` 必然接地失败
- 执行统计 stats 增加 `pending` 计数(ABORTED 时未触达节点不再从统计中消失)

## [0.4.0] - 2026-08-15

### 前沿机制升级 P1 批次(双评估器 + LLM 反思)

#### Added (新增)
- G2 双评估器接入 `/refine`(验证器不对称原则落地): 评估器A=规则(adapter.validate),
  评估器B=`LLMJudgeEvaluator`(LLM-as-a-judge,rubric 四维打分,可用更廉价模型) —
  分差 ≤15 取均分进棘轮;分差 >15 触发第三评审(tiebreaker persona)仲裁取中位数。
  `judge_with_peers`/`arbitrate` 从零调用状态激活
- `RatchetGate.judge_reports`: 对已完成评审报告的统一双评审判定(不重复调用昂贵评估器),
  `judge_with_peers` 委托实现,单报告时降级为普通 judge
- `LLMJudgeEvaluator`(`core/llm_judge.py`): reviewer/tiebreaker 双 persona,
  防御性解析(分数钳位/字段校验),失败返回 None 由调用方降级单评估器 — 评审失败不阻断精化
- LLM 反思升级(Reflexion 完整版): REVERT/EARLY_STOP 时由 LLM 生成结构化教训
  (目标/操作/判定/评估问题输入),失败降级 P0 的确定性模板
- MockLLMProvider 新增 独立评审官/仲裁评审官/复盘 三条路由,dev 模式全链路可离线验证
- `GOAL_STUDIO_DUAL_EVAL=0` 可关闭双评估器(回退单评估器)
- Voyager 技能库(能力复利): `/validate` PASS 的会话自动沉淀
  `(goal, L1 骨架, 分数)` 为 procedural 记忆(category=pattern,
  key=pattern:{session_id} 重复验证覆盖不堆积,置信度随分数提升);
  `/draft` 按目标相似度检索 Top-2 成功结构,经
  `BaseAdapter._prior_patterns_section` 渲染为 few-shot 注入四个适配器 —
  验证通过的结构成为新目标的构建块,坏条目跳过不阻断
- Voyager 深化: build 成功后产物骨架(md 标题/py 签名,每文件≤20 行、
  总≤60 行)合并进 pattern 的 `artifact_outline` 并重算向量、置信度+0.02;
  few-shot 渲染骨架预览段,并显式提示"可整体复用某参考结构的节点子集"
  (组合性);CLI 新增 `goal-studio skills` 列出技能库(目标/分数/L1/骨架/时间)
- REST 限流开关 `GOAL_STUDIO_RATE_LIMIT=0`(生产默认开启);server 测试
  conftest 全局禁用(全量回归 >60 POST/min 会触发 429 误报)

### 前沿机制升级 P2 批次(条件边语义 + embedding 检索)

#### Added (新增)
- 条件边语义贯通(此前 `edge_type` 为死字段): draft prompt 支持
  `edge_type=condition + condition 表达式`(同节点多条出边构成分支);
  parse/refine-ADD_EDGE 校验缺条件文本降级 data 边(不丢图连通);
  build prompt 与 fallback 编译为 `next_branches: [{when,next},{default}]`
  分支数组(data 单出边保持 next 字符串);validate 分支维度认可显式条件边;
  mock workflow DAG 提供条件边离线示例
- `core/embedding.py`: EmbeddingProvider 协议 + HashEmbeddingProvider
  (确定性词袋哈希,零依赖离线) + OpenAIEmbeddingProvider(可选依赖)
  + cosine_similarity + 工厂 `get_embedding_provider()`
- 检索排序升级 `_rank_entries`: embedding 余弦优先(复用沉淀时缓存的向量,
  缺失现算不回写),不可用/异常整体降级关键词路径 — 默认
  `GOAL_STUDIO_EMBEDDING_PROVIDER=none` 保持零行为变化,lessons/patterns 共用
- pattern/reflection 沉淀时经 provider 缓存向量到 entry.metadata

#### Changed (变更)
- `_retrieve_lessons`/`_retrieve_patterns` 均委托 `_rank_entries` 统一排序

#### Changed (变更)
- `/refine` 端点改为 DI 注入 LLM provider(`Depends(get_llm_provider)`),
  测试可 override 为 None/Fake
- 双评审 issues 合并进入反思输入与响应(merged report)

### 前沿机制升级 P0 批次(功能完整可用性 + Reflexion 经验闭环)

#### Added (新增)
- G1 知识沉淀闭环落地(Reflexion 机制): `KnowledgeStore` 从零调用方变为核心回路 —
  refine 触发 REVERT/EARLY_STOP 时自动生成结构化失败反思写入 experience 区;
  draft 前按目标关键词重合度检索 Top-3 历史教训注入 prompt(`prior_lessons`),
  四个内置适配器均已接线(`BaseAdapter._prior_lessons_section`)
- `LocalFileKnowledgeStore`: JSON 文件持久化实现(原子写 + 线程锁 + 损坏降级),
  路径 `GOAL_STUDIO_KNOWLEDGE_ROOT` 环境变量可配
- `POST /api/sessions/{id}/export`: Web 端产物导出(默认产物库同级
  `goal-studio-exports/{id}/`,支持自定义 file URI)
- `POST /api/sessions/{id}/install`: Web 端产物安装(target=file/maxfun,
  maxfun 经 plugins 目录动态加载,缺失时返回 400 指引)
- 前端工具栏新增 撤销/导出/安装 按钮;`useSession` 暴露 `undo/exportArtifact/installArtifact`

#### Changed (变更)
- `POST /ratchet/undo` 响应扩展为 `{graph, snapshots_remaining, phase}`,
  前端据 `snapshots_remaining` 维持撤销按钮可用态
- export/install 成功后会话进入 `DONE` 阶段(终态可达);前端徽章补 done/draft
- build 后自动刷新会话(取回 `best_artifact_id` 驱动导出/安装按钮)

#### Fixed (修复)
- 前端 Web 无法撤销(REST 已有但未接线)与 build 后无法获取产物两大闭环断点

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
