---
name: 沪上错题
version: 1.6.0
description: >
  该技能为上海高中生提供错题全生命周期管理：拍照录入错题，由当前会话多模态模型识别
  并归因到沪教版知识点（六类双轴错因+三栏记录），按记忆规律自动排期复习，遮答案重做、
  关键点匹配销号，连错升级并生成变式练习，输出长图周报/考前错题簇与可打印题答两册。
  支持上海 3+3 等级赋分"掉级成本"分析与学科动态权重。
  使用场景：(1)记错题/录错题 (2)今日复习 (3)重做后判分销号 (4)给错题出变式/找同类题
  (5)周报/一模报告/等级考掉级分析 (6)设置年级选科目标等第 (7)更新政策。
  触发词：记错题、错题本、今日复习、判分、销号、变式、错题簇、掉级、等级考复习、沪上错题。
triggers:
  - pattern: "记错题|录错题|错题录入|这批错题|这几道错题"
    description: "错题照片录入工作流"
  - pattern: "今日复习|到期的错题|复习清单|该复习什么"
    description: "复习排期清单"
  - pattern: "判一下|判分|重做完了|再做一遍|销号"
    description: "重做判分与掌握态流转"
  - pattern: "变式|出几道同类|同类题|相似的题"
    description: "变式生成/同类题匹配"
  - pattern: "错题周报|错题报告|一模分析|掉级|等级考.*(目标|等第)|错题簇"
    description: "报告与等级考掉级分析"
  - pattern: "沪上错题|设置.*(年级|选科)|更新政策"
    description: "技能设置与政策更新"
---

# 沪上错题 · 上海高中错题全生命周期管理

为上海高中生把"错题总结"变成"错题管理闭环"：录入零抄写、归因到沪教版知识点、
按记忆规律驱动重做销号、等级考掉级成本可视。**所有智能环节（识别/归因/判分/变式/复核）
由当前会话所选模型执行（用 read_image 看图），不固化任何模型供应商、不需要 API key；
确定性环节（查重/排期/状态机/统计）由 scripts/cuoti.py 完成。**

## §1 命令入口

| 动词 | 用户说法示例 | 跳转 | 前置 |
|---|---|---|---|
| ①录错题 | 发1~N张照片+"记错题/这是月考数学" | §3 | 首次运行先 §2 初始化 |
| ②今日复习 | "今日复习" | §4 | — |
| ③判分 | 重做后再拍草稿+"判一下" | §5 | 需给出 wa-id 或由复习清单带入 |
| ④变式 | "给wa-xxx出变式/找同类题" | §6 | — |
| ⑤报告 | "周报/一模报告/掉级分析" | §7 | — |
| ⑥设置 | "设置：高三，选科物化政，目标物理B+" | §2 | — |
| 辅助 | "更新政策" | references/grading-policy.md 复核指引 | — |

自然语言不在表中时按语义就近路由；同一轮可串联多动词（录完顺手给今日清单）。

## §2 数据与初始化

数据目录单点：`$CUOTI_HOME`，缺省 `~/.hushang-cuoti/`。结构：

```
~/.hushang-cuoti/
├── config.json          # 学生档案：年级/选科/目标等第/推送开关
├── questions.jsonl      # 题目库（append-only）
├── wrong-answers.jsonl  # 错题卡（含掌握态状态机字段）
├── review-log.jsonl     # 每次复习判分流水
├── variants.jsonl       # 通过复核门的对外变式池
├── profiles.jsonl       # 画像快照（append，读最新）
├── media/               # 原图与裁剪图
└── reports/             # 长图/docx 产出（日期命名，旧件移 archive/）
```

首次触发任意动词：

```bash
python3 ~/.agents/skills/沪上错题/scripts/cuoti.py init
```
脚本仅用标准库。`init` 幂等；已有 config 时用 `set` 子命令增量更新字段。
下文 `cuoti.py` 均指 `python3 ~/.agents/skills/沪上错题/scripts/cuoti.py`。
**隐私红线：全部数据只存本地；任何外发（飞书推送）必须 config.push_enabled=true 且当轮用户明确要求。**

## §3 动词①：录错题（intake 管线）

完整协议见 `references/intake-protocol.md`，主干五步，批量照片逐张执行：

1. **预校验**：用 read_image 看图，判定 `is_exam_content`（题目/作答/批改痕迹）。非题内容（风景、
   聊天记录、纯印刷书封）→ 拒录并一句话说明，不进库。
2. **结构化识别**：读 `references/subjects/{学科}.md` 知识块表，输出 intake JSON（字段契约见
   intake-protocol §2）：题面文本、学生答案、正解、错因六类双轴标注、知识点ID（必须来自受控表；
   表中确无匹配时用 `grow:` 前缀候选名）、考卷轨（周测/月考/期中/期末/区模考/等级考/合格考/自练）、
   每题 `confidence` 自评（high/low）。**学生手写答案看不清时禁止猜**——标 `needs_confirmation`
   列出疑问字段，问用户一句话确认，确认前该题入"暂存区"不参与排期。
3. **交叉校验**（条件触发，非全量）：`confidence=low` 或含大量手写 → 若环境有 `image_ocr` 工具则
   跑一遍 OCR，文本相似度明显不一致 → needs_confirmation。高置信直通（省 token 原则）。
4. **查重**：`python3 scripts/cuoti.py dedup --image <路径>`（图md5）+ 题面归一化指纹，
   命中重复 → 提示复用旧卡不新增。
5. **入库**：填完整错题卡（`templates/wrong-answer-card.md` 结构，含 `image` 原图路径、
   `origin` 来源标记）→ `cuoti.py add --file <JSON>`。
   脚本自动：建 Question+WrongAnswer 双向链（均写 student）、原图复制入 media/、赋 wa-id、
   直接置 `scheduled` 并首排期（1天后）；知识块册次高于学生学期 → 返回
   `unlearned_flags` 未学先练预警（学期×册次矩阵，由 subjects 表"已核实"教材锚点驱动），核实进度后继续。
6. **判分守门**：hold 状态卡片**禁止直接 grade**（脚本硬拒），必须先 confirm。

录入汇报格式：`✅ 收录 N 道（数学3/物理1），待确认 M 道，跳过 K 道（原因）`，附各 wa-id；
有 unlearned_flags 时置顶提示。

### §3B 整卷模式（试卷+答题纸批量匹配）

用户发**整套试卷照片 + 答题纸照片**说"这整卷分析一遍"时，走五步：

1. **试卷骨架识别**：逐页读试卷图 → 输出 exam JSON（id/name/date/subject/total/
   questions[{no, text_brief, qtype, points}]）→ `cuoti.py exam-init --file`。题号以试卷印刷为准。
2. **答题纸识别与逐题判定**：逐页读答题纸 → 每题得 {作答转写, 批改痕迹}。**判对错优先级：
   教师批改符号/得分 > LLM 自判**（LLM 判的标 graded_by=llm，进 needs_confirmation 汇总）。
3. **对齐匹配**：按题号把答题内容挂回试卷骨架；跨页断题、题号错位、一题多问拆不准 → 该题
   needs_confirmation，**禁止猜对齐**。
4. **逐错题独立入库**：每道错题生成一张完整 intake 卡（带 exam_paper_id/qno/points_max/points_got，
   走 §3 同一套 G1-G5 门控），correct_answer 落笔前程序验算（理科必跑）。
5. **整卷报告**：`cuoti.py exam-summary --id ex-xxx` 出逐题对错/得分/丢分表（数字全派生）→
   每道错题按四段式独立分析（含**原题**节）→ 合并渲染一份 PDF。

## §4 动词②：今日复习（排期引擎）

```bash
python3 ~/.agents/skills/沪上错题/scripts/cuoti.py plan [--subject 数学] [--exam-mode] [--limit 8]
```

输出到期清单（按学科动态权重排序：等级考科目 > 统考 > 合格考轻量；重点追踪项置顶），每条含：
wa-id、知识块、上次断点描述、**掉级成本**（band_points 数值字段，报告可直接求和）、未学先练标记。

复习方式硬规则（测试效应）：呈现只引用记录的 `question_text` 字段（答案与骨架字段一律不显示；
原图 media/ 存档备查，如原图含答案区则先文字重述题面而非直接展示原图），
学生重做后进入 §5 判分。禁止"浏览式复习"；学生可说"只看错题不重做"，此时判分参数用
`--result browse`，只记流水**不推进状态机**。

## §5 动词③：判分与销号（状态机）

规则权威源：`references/error-taxonomy.md` §3 与 `references/schedule-spec.md`。流程：

1. 收学生重做结果（照片草稿或文本答案）。
2. **双条件判定**：(a) 结果正确；(b) 过程关键点匹配——对照入库时的正解骨架（Key-Steps），
   语义匹配判"断点是否真修复"。防"碰巧对/抄后重做"假掌握。
3. AI 置信不足（判分依据模糊/草稿残缺）→ `cuoti.py grade --id wa-x --result pending_confirmation`，
   向用户确认，**不自动销号**。
4. 判定入库：`cuoti.py grade --id wa-x --result pass|fail --keypoints ok|partial|bad`。状态机：

```
scheduled ⇄ reviewing ┬ pass(关键点ok)×2连对 → mastered ─wake_pass×3(30/90/180)→ archived(静默)
                      ├ pass但keypoints=bad|partial → reviewing（pass-but-broken：连对归零、连错不加、1天重排）
                      ├ fail×1 → scheduled（间隔重置1天，连错+1）
                      └ fail×2连错 → escalated（重点追踪：清单置顶+自动建议变式）
```

归档≠删除：mastered 项按 30/90/180 天"远期唤醒"再排期（grade --result wake_pass 推进阶梯，
第三次通过才转 archived 静默），日常清单不刷屏，仅考前模式（`--exam-mode`）把到期唤醒项
合并展示。hold 态必须先 confirm 才能 grade（脚本硬拒，防绕过确认门）。

## §6 动词④：变式与同类题（找+造双轨）

- **找**：`cuoti.py similar --id wa-x` 取候选（同知识块ID优先，文本词面相似兜底）；有匹配先给旧题。
- **造**：候选不足时，LLM 生成变式，约束见 `templates/variant-prompt.md`（原创改编禁抄原题、
  同知识块、同难度±1、题型配比 3选择+2填空+1解答、答案与解析分离）。
- **复核真门（仅卡对外输出）**：要打印/发给学生的变式卷必须走独立复核——派发一个子代理会话，
  按 `references/review-gate.md` 逐题真读+引用题面证据+解析完整性自检，FAIL 题不入库、CRITICAL
  （疑似搬真题/超纲）删换。自用流程不启用独立复核（省token）。
- 真题红线分级：**学生自用**错题本可收录自己考过的真题（source_type=own_exam，不视为违规）；
  **对外输出**（练习卷/变式/预测）禁搬运真题，复核门内做指纹相似度检查。

## §7 动词⑤：报告（多角色输出）

```bash
cuoti.py stats          # 确定性统计：按学科/知识块/错因六类的计数、状态分布、掉级成本汇总
```

- **周报**（学生端）：stats 结果 + LLM 增量画像（读最近画像做摘要合并，**杜绝全量重算**）→
  填 `templates/weekly-card.html.tpl` → 调 `doc_render_longimage` 工具渲染竖版长图 PNG →
  present/dsh_im_return_file 交付；config.push_enabled=true 且用户要求时用 `file_push_lark` 推送。
- **考前错题簇报告**（考前模式）：`plan --exam-mode` + 按"边际收益最大簇"排序：
  等级考科目按 3分/级换算掉级成本（`references/grading-policy.md`），指出"补这5道题≈半级"。
- **错题深度分析报告**（用户说"详细分析这道题/分析报告"时默认，每批首次总结也用它）：
  按 `templates/deep-analysis.md.tpl` 四段式——①考点解析（逐小问：知识块ID→课标条款→考查定位→
  命题意图；新定义题必给"定义的数学本质一句话"）②可能犯的错误分析（题型通病清单 E1..En：
  表现→X类/KLS层→触发条件，标注本卷**已踩/已避开**，避开的点名表扬）③本次答题错误原因分析
  （逐卡三栏+双轴+根因两层追问"为什么这一步想不到"）④完整正确解题过程（考场满分书写版：每步
  标依据、分类讨论给完备性声明、关键步插【易错回扣】；**数值结论必须先程序验算再落笔**；
  完整解答在学生确认重做完毕后才展开，防"看答案=会了"）。
- **两册打印**：按 `templates/practice-paper.md.tpl` 组卷 → `word_processor` 工具生成
  `…题目.docx`/`…答案.docx` 两册（答案册含思路/考点/易错点，不重复题面），日期命名+archive 归档。
- **考点簇定期总结**（用户说"簇总结/考点总结"，或建议每周一次）：`cuoti.py clusters --days 30`
  取开放错题 Top 簇（按丢分排序，含错因分布与断点样本）→ 对每个入选簇出"簇深潜"：
  共性错因归纳 → 该考点精讲（知识块→课标条款→题型套路）→ 3 道变式（走复核门）。
  可提议配周历（cron_create 每周日提醒跑簇总结，开启须用户确认）。
- 报告内所有数字**必须来自 stats/plan/clusters/exam-summary 字段输出，禁止凭印象填数**（初中版假门教训）；
  画像/报告里给家长的建议不超过3条可执行动作。

## §8 学科范围与建用结合

v1.1 精建四科受控知识块表（**教材锚点已逐册实证，2026-10-16 核实组，来源见 grading-policy §5**）：
**数学（沪教版2020 七册制）/ 英语（沪教版2020 必修一二+2025届起笔试115+听说35）/
物理（沪科技版2020 六册制）/ 化学（沪科技版2020）**，见 `references/subjects/*.md`。其余科目
（语/生/政/史/地）：识别环节自动生长 `grow:` 候选知识块，`cuoti.py kp-candidates` 列出待并条目；
月度并树按 `references/schedule-spec.md §2b` 执行：先给 subjects 表新增正式 ID 行（下一空号+教材
锚点必填），再 `cuoti.py migrate --from "grow:X" --to 新ID` 同步改写两库。新增精建学科时复制
subjects 模板结构即可（学科插件化）。
注意：语数外**只设合格考**（高考为统一文化考试科目），band 掉级轴仅适用于选定的 3 门等级考科目。

**基准定义（用户裁决，权威口径）**：本技能知识系统以「2026 高一在用教材（2020版）+ 大纲」为唯一标准——
三层文件（国家课程标准2017版2020修订 / 上海课程实施方案沪教委基〔2021〕35号 / 当年考试文件）与冲突裁决
见 `references/grading-policy.md §0`；四科知识块均带课标条款映射（各 subjects 表"课标映射"节）。

## §9 中断恢复

任何步骤被压缩/中断后：读 `~/.hushang-cuoti/` 各 jsonl 的行数与最新条目即完整状态
（append-only 设计，无隐藏中间态）；`cuoti.py status` 返回 未确认暂存 / 今日到期 / 重点追踪 计数，
据此向用户报告"上次到哪、还差什么"，不重复入库（查重保障）。

## §10 平台集成（DSH 优先，MaxFun 守卫跳过）

- DSH 工具依赖：`read_image`（识图，模型=会话下拉所选）、`doc_render_longimage`（长图）、
  `word_processor`（两册docx）、`image_ocr`（条件交叉校验）、`file_push_lark`+`present`/
  `dsh_im_return_file`（交付）、`subagent`（独立复核真门）。全部工具缺失时降级：
  长图→Markdown 报告、两册→PDF via pdf_generator、复核→自检清单+用户确认。
- 画像快照与关键结论可用 `memory_save` 沉淀（kind=auto），供跨会话调用。
- MaxFun 平台操作（wiki/pinned/experience/evolution）以 `test -d {project}/.claw` 为守卫，
  非 MaxFun 环境自动跳过，不影响本技能核心流程。守卫内约定：
  - **Wiki 域** `education/cuoti/`：仅三个关键节点同步（年度政策更新 / 月度并树 / 季度画像）。
  - **Pinned**（预算≤4，含清除时机）：PERM=`学生档案摘要`(「设置」动词变更时覆盖)；
    PERM=`掉级换算口径`(policy更新时覆盖)；TASK=`考前窗口与积压计数`(每次报告后刷新)；
    STAGE=`本次待确认字段清单`(当轮任务结束即清)。同键写入即覆盖，禁止只增不换。
  - **经验 JSONL**：路径 `~/.claw/evolution/experiences/hushang-cuoti/{YYYY-MM-DD}.jsonl`；
    字段 `{id:"cuoti-exp-{unixMs}-{rand4}", timestamp:<Unix毫秒数值>, category:"cuoti",
    toolName:"intake|grade|variant-import|gate|report", summary:"<一句话>",
    tags:["subject:物理","state:mastered","kp:P13","verdict:PASS","quality:…"]}`——
    可分析值一律编码进 tags（N-gram 可捕获，防 meta 孤岛）。
  - **PostCompact 协作**：本技能状态真相在 `~/.hushang-cuoti/`（append-only），压缩后先跑
    `cuoti.py status` 取计数再向用户复述"上次到哪"，不依赖会话记忆恢复；与 harness 压缩钩子
    无写冲突（技能不写 .claw 状态区，只读取自身 JSONL）。

版面细则见 `references/output-spec.md`；科目运行时语义见 `references/subject-activation.md`。

## §11 质量自检（每次发报告前）

- [ ] 报告数字全部可追溯到 stats/plan 输出（含 `open_band_cost_by_subject` 与 `weak_clusters`）
- [ ] 待确认项未被计入掌握统计（stats.hold_pending 与叙述一致）
- [ ] 对外变式全部 `verdict=PASS|WARNING-CONFIRMED`（variant-import 默认拒绝其余值，可复验）
- [ ] 未选科目/合格考内容未混入等级考掉级分析
- [ ] 隐私：未向任何外部通道发送数据（除非 push_enabled 且用户当轮要求）
- [ ] 多学生场景：当前 config.student 与本轮操作对象一致（清单/统计/检索按 student 隔离）

注：本节自检由**生成该报告的同一会话**执行（软门，成本低）；**对外交付物**（变式/练习卷）
另走 review-gate **独立会话**真门——两道门分工不同，不可互替。
