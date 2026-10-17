# 录入门控协议（intake-protocol v1.0）

## 1. 五步管线与门控点

```
照片 → [G1 预校验] → [G2 结构化识别] → [G3 条件交叉] → [G4 查重] → [G5 入库/暂存]
```

- **G1 预校验**：read_image 快判是否含题目/作答。判据：印刷题干、手写解答、批改红笔、答题卡。
  非题内容直接拒录（一句话说明），不建记录。成本：每图一次轻量看图，不跑全文提取。
- **G2 结构化识别**：输出 intake JSON（§2契约）。一次性处理整页多题时逐题拆分；
  一张图含多题必须拆成多条记录（**题粒度录入**——市面产品"一拍整页"是被投诉的痛点）。
- **G3 条件交叉校验**（不是全量！省token原则）：仅当 ①G2自评 confidence=low，
  ②手写占比高且关键数字存疑，③用户明说"仔细核" 之一成立，调用 `image_ocr` 工具取文本层，
  与 G2 题面做归一化对比；不一致字段列入 needs_confirmation。**image_ocr 不可用时降级**：
  对 low 置信字段直接向用户提问确认，绝不带疑入库。
- **G4 查重**：`cuoti.py dedup`。双指纹：原图 md5（重拍同图）+ 题面归一化文本
  （去空白/标点/全半角折叠后 sha256 前16位，另存 bigram 集合供近似比对）。
  精确命中→提示复用；相似度>0.85→"疑似已录，是否复用旧卡？"交用户。
- **G5 入库**：needs_confirmation 非空 → 记录状态 `hold`，不排期不入画像；全部确认清零后
  `cuoti.py confirm --id` 转 `scheduled` 并首排期。确认前 grade 会被脚本硬拒（防绕过门）。

## 2. intake JSON 字段契约

```json
{
  "subject": "数学|英语|物理|化学|语文|生物|政治|历史|地理",
  "student": "可选，缺省=config.student（多孩子共用一库时必填，plan/similar/dedup/add/stats 全链路隔离）",
  "exam_track": "周测|月考|期中|期末|区模考|等级考|合格考|春考|秋考|自练",
  "exam_date": "YYYY-MM-DD 或 unknown",
  "source_type": "own_exam|homework|predicted_practiced|other",
  "image": "原图绝对路径（脚本自动复制存档入 media/；无图填null）",
  "origin": "real|demo|watch（demo=举例错误永不计掌握；watch=学生会说'当时失误'，跳过排期仅考前出现一次）",
  "question": {
    "text": "题面全文（LaTeX友好，公式保留原文）",
    "figure_ref": "图注或'见原图'，无则null",
    "qtype": "选择|填空|解答|实验|论述|概要写作|翻译|写作|其他",
    "kp_ids": ["M03","M13"],
    "difficulty_self": "基础|中档|压轴",
    "confidence": "high|low"
  },
  "student_answer": "学生当时解答（照片转写，看不清标[?]+needs_confirmation项）",
  "correct_answer": "标准答案（卷上有批改则转写；无则LLM求解并在basis注明'AI解答未经订正核对'）",
  "wrong": {
    "x_tags": ["X4"], "x_primary": "X4", "kl_s": "L",
    "score_axis": {"type": "abs|band", "points": 1.5, "desc": "卷面-4分 / 掉半级≈1.5分"},
    "breakpoint": "第3步……拐错弯（三栏记录栏二）",
    "key_steps": ["骨架1","骨架2","…3-6条（栏三，判分与变式对照物）"]
  },
  "needs_confirmation": ["student_answer: 第2行数值", "…"]
}
```

规则：
- kp_ids **必须取自** `references/subjects/<科>.md` 受控表；确无匹配才允许 `grow:候选名`，
  月度并树按 schedule-spec §2b：subjects 表新增正式ID行 → `cuoti.py migrate --from grow:X --to 新ID` 两库同步改写。
- score_axis.points 为**数值**（stats/plan 直接求和排序）；desc 仅展示。等级考科目经验口径：
  掉半级=1.5、掉一级=3（换算规则在 grading-policy，年检可修）。
- **未学先练检测**（学期×册次矩阵）：`add` 按 config.grade 学期（支持"高一上/高一下/高二上/高二下/高三"；
  裸"高一"保守按高一上并提示细化）对照 subjects 表"已核实"教材锚点册次——知识块册次高于当前学期
  → 返回 `unlearned_flags`，向用户核实教学进度后照常入库（清单带标）。实测例：高一上录化学"钠及其
  化合物"（必修二）→ 预警；录"物质的量"（必修一）→ 放行。物理电学基础在必修三（高二上）同理。
- `add` 输出四桶：added / held_pending_confirmation / skipped_duplicate / unlearned_flags。
- correct_answer 若学生卷面有红笔订正→以订正为准并转写；若需 LLM 自行解题，须给出完整自洽解法
  且 key_steps 与解法一致（这是判分对照物，错了会连锁污染销号判定——最高注意级字段）。
- 一道题学生全对但"做得很险"（蒙对边缘）：用户可要求记 `watch` 型（不占复习排期，进报告提示）。

## 3. 批量与节奏

- 支持一次多张（月考全卷10~20题常见）。逐张跑 G1-G4，G2 分批防超长输出。
- 单批>6张时先给进度回执（"3/8已识别"），避免静默。
- 录入汇报后主动附一句："今日已有 N 道到期复习，需要先看复习清单吗？"
