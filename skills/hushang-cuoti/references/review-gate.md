# 对外输出复核真门协议（review-gate v1.1）

> 适用对象：**对外输出**的变式题/练习卷/预测练习。学生自用错题录入与自用复习不走此门（省token）。
> 初中版教训：复核"假门"——模板填数、0题解析失败仍出报告、统计与被审内容相反。
> 本协议由三层保障：①契约YAML单一同源 ②代码不变量（`cuoti.py variant-import` **默认拒绝**：
> verdict 字段缺失或不在白名单的变式一律拒入并回显拒绝原因）③逐题证据引用要求（无证据=无效复核）。

## 1. 契约单一同源

复核维度定义**只能**来自本文件 §2 的 YAML 契约；派发复核子代理时把契约原文注入 prompt，
禁止两处各写一套维度（契约即代码原则）。

```yaml
review_contract: v1.0
dimensions:
  - id: D1_outline     # 大纲/课标匹配：知识块ID正确、未超该生教材进度(以config年级+知识块教材锚点判)
  - id: D2_quality     # 题目质量：题面自洽、数据合理、条件充分、答案正确且与解析一致
  - id: D2a_parse_ok   # 解析完整性自检：题面能被完整读出（先于D2执行；读不出=无法判定，不判低分）
  - id: D3_source      # 来源合规：原创改编声明、与真题指纹相似度低于阈值、非整题搬运
  - id: D4_difficulty  # 难度：标注与内容一致、在锚题±1档、变式梯度成立
verdicts:      # 字面令牌（cuoti.py variant-import 白名单=PASS/WARNING-CONFIRMED，其余含缺省一律拒）
  PASS: 全维true(允许≤1条warn注记)
  WARNING: ≥2 warn 或 1条无法判定 → 列出待修项，人工确认后才可入库
  FAIL: 任一维明确false → 该题退回重写，禁止入库
  CRITICAL: D3含真题搬运 或 D1超纲 → 立即删题替换，全卷连坐复审
evidence_required: true   # 每一维判定必须附"引用被审题面/答案的原文片段"，无证据=无效复核
```

## 2. 执行流程（派发独立子代理会话）

1. 待审内容写成 Markdown 文件（每题带稳定 local_id）。
2. 派 `subagent`：prompt = 本契约 YAML + 学科知识块表路径 + 待审文件路径 + 指令"逐题独立审，
   先做 D2a 解析完整性自检，每题输出 JSON（维度/判定/证据引用），不许参考生成过程的任何说辞"。
3. 回收逐题 JSON → 任一题无 evidence 引用 → 该题复核作废重跑（防"只填结论"）。
4. 汇总：`cuoti.py gate-record --file <review结果.json>` 落 review-log（kind=gate 与判分流水
   分键，stats 互不干扰）；FAIL/CRITICAL 题剔除；WARNING 题需用户确认后把 verdict 改写为
   字面值 `WARNING-CONFIRMED` 才可 `cuoti.py variant-import`（脚本默认拒绝其余一切值，含缺失）。
5. 报告数字**由逐题 JSON 派生**（题数、通过率），禁止手写统计句（防假门统计话术）。

## 3. 与生成会话的角色分离

生成变式的会话不得自称"已复核"。复核必须在另一个 agent 会话（subagent）里发生，且生成内容
与复核判定之间只通过文件传递事实，不传递理由（防锚定；本机评审经验：主审易把自身怀疑塞进事实包）。

## 4. 降级路径

subagent 不可用/超时 → 复核改由当前会话执行，但必须显式声明"非独立复核"并输出逐题证据引用，
且产出物标注 `review=non-independent`。用户确认后方可使用。复核彻底失败 → 本批变式标
"未复核"，只可自用预览，禁入打印卷。
