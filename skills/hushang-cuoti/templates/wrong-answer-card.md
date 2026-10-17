# 错题卡模板（wrong-answer-card v1.0 · 三栏记录法）

录入时由模型按 intake-protocol 契约填充；`cuoti.py add` 存 JSONL，卡片是本记录的人读视图
（按需展开给用户，一题不超过一屏）。

```markdown
## wa-{id} · {学科} · {知识块ID+名} · {考卷轨} {难度}

**① 错解现场**（原文/照片，不评论）
- 题面：{question.text | 图见 media/xxx.png}
- 我的解答：{student_answer}

**② 错因编码**
- 能力轴：{X_}(主) {X_}（辅）｜ 三层：{K|L|S} ｜ 计分轴：{abs:-4分 | band:≈半级/1.5分}
- 断点：我在第{__}步拐错弯——{breakpoint一句话}
  （"粗心"不允许单独出现，必须写成：审题漏看/抄写错/跳步算错/时间挤压 之一）

**③ 正解转折点**（Key-Steps 骨架，判分与变式的对照物；非完整解析）
1. {骨架1}
2. {骨架2}
3. {骨架3}

<details>参考解答（重做对之前折叠不显示）
{correct_answer 全文}
</details>

状态：{new|scheduled|reviewing|escalated|mastered|archived} · 复习记录：{pass/fail史}
```

英语主观题变体：栏三改为"要点清单"（概要写作=原文要点4-6条+转换说法；翻译=限定信息核+目标搭配）；
听说题改"复述要点+改进项"。
化学/政治流程论述题变体：栏三按"采分链"写（观点→依据→推理→结论 各一行为一采分点）。
