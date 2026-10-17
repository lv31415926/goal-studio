# 输出规范（output-spec v1.0）

## 1. 双形态原则

| 形态 | 场景 | 生成方式（DSH 工具优先） |
|---|---|---|
| 竖版长图卡片 | 每日复习清单、周报、考前簇——手机看、家长转 | 填 templates/weekly-card.html.tpl → `doc_render_longimage`（宽度1080 scale2） |
| 可打印两册 | 变式练习卷、重做卷 | `word_processor`：`…题目.docx`+`…答案.docx`；降级 `pdf_generator` |

工具缺失降级链：长图→Markdown 报告；docx→pdf_generator→Markdown。任何降级要告知用户。

## 2. 命名与归档

- `reports/` 内文件一律 `<类型>-<YYYY-MM-DD>.(png|docx|md)`，如 `weekly-2026-10-16.png`。
- 同名旧件先移 `reports/archive/`（保留全部历史，内容型产出可追溯）。
- 两册命名：`{policy_year}沪上错题[学科][主题]题目.docx / …答案.docx`（年份取 config.policy_year，
  未核对前一律用"未核对"占位，禁止凭印象写死年份）。

## 3. 长图卡片信息结构（自上而下）

1. 头部：学生名+日期+距最近考试倒计时（config.next_exam，标"预估"）
2. 今日清单（按 schedule-spec 排序，每条：学科徽章/知识块/错因tag/掉级成本注记/状态）
3. 本周概览：收录N/复习N/销号N/升级追踪N ——**数字全部来自 cuoti.py stats，脚本产 JSON 直接注入**
4. 薄弱簇 Top3 与建议动作（≤3条）
5. 页脚：政策口径年份（grading-policy policy_year）

样式基线：深蓝金或浅墨白（用户可在设置切换），中文字体由渲染管线处理，不在技能内硬编码字体路径
（修复初中版 STHeiti 硬编码缺陷）。

## 4. 两册规范（沿用初中版实测有效的资产：题目/答案分离与图示程序化生成）

- 题目册：题号+题面+（需要图的）嵌入图+答题留白；**不含**答案/解析/知识点（同册即失效）。
- 答案册：题号对应+答案+解题骨架+考点(知识块ID)+易错点+（变式卷）出题依据标注。
- 排版：题目/答案各自独立成文件，学生打印题目册即可重做。

## 5. 交付动作

- 会话内产出：`present` 声明为交付文件；移动端需要发送时用 `dsh_im_return_file`。
- 飞书推送：**仅当** config.push_enabled=true 且用户当轮明确要求，用 `file_push_lark`。
  推送失败照实转述，不虚报"已发送"。
