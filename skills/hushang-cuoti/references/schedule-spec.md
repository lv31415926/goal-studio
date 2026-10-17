# 复习排期与掌握态算法（schedule-spec v1.0，cuoti.py 实现口径）

## 1. 状态机（唯一权威图，与 cuoti.py 实现一致；SKILL.md §5 为速览）

```
hold(待确认) ──confirm──► scheduled ⇄ reviewing ─┬ pass_streak≥2 且关键点ok → mastered ─wake_pass×3→ archived
                                                 ├ pass-but-broken(结果对但关键点bad/partial)：pass_streak归零、fail_streak不变、interval重置1天、state=reviewing
                                                 ├ fail：fail_streak+1，pass_streak归零，1天重排；fail_streak≥2 → escalated（清单置顶）
                                                 └ browse / pending_confirmation：只记流水，不推进
```

- 任何判分先写 review-log，再改状态，可审计可回放。
- `browse`（只看不错题重做）只进 log 不推进。
- `pending_confirmation` 判分结果不入状态机。

## 2. 复习间隔（v1 固定表；字段预留 FSRS v2 接入）

| 事件 | 下次间隔 |
|---|---|
| add/confirm 首排 | 1 天 |
| pass 第1次（关键点ok） | 3 天 |
| pass 第2次 → mastered，30 天后远期唤醒 | 30 天 |
| mastered 后 wake_pass | 90 天，再 pass 180 天，第三次 wake_pass → **archived（终态静默）** |
| pass-but-broken（结果对关键点坏） | pass_streak 归零、fail_streak 不变、重置 1 天、state=reviewing |
| fail | fail_streak+1、pass_streak 归零、重置 1 天；fail_streak≥2 → escalated |
| escalated 项 | 清单置顶（下次复习第一梯队+自动建议变式） |

**archived 为终态**：grade 任何结果一律拒绝（防"静默复活/脏流水"，L3审计N5）；想再练该题走
「变式」或对同一题重新录入（查重会提示复用）。历史流水永不清算——stats 只计当前学生卡池 join
后的 advance 记录（学生隔离+demo排除双重过滤，L3审计N2）。

FSRS v2 接入位：记录每卡 (D,S,R) 三参数占位字段已留，届时用 open-spaced-repetition 实现替换本表，
历史 review-log 可直接拟合，无需重录。

## 2b. grow 候选并树迁移协议（月度，L3审计N6）

1. `cuoti.py kp-candidates` 列出全部 `grow:` 候选与频次。
2. LLM/人工裁决归一：并入既有正式 ID，或在对应 subjects 表**新增一行**（科前缀+下一空号，
   教材锚点列必填——"待核"字样允许，问号字样不允许）。相近候选名合并到同一目标。
3. `cuoti.py migrate --from "grow:X" --to P25`——questions 与 wrong-answers 两库 kp_ids 同步改写
   （stats 簇键由记录现算，迁移后自动连续；plan 未学先练判定随新锚点生效）。
4. 迁移后 `kp-candidates` 应清空该候选；报表注明本次并树映射表。

## 3. 每日清单排序权重

`score = w_exam(w等级考) × (1 + streak_fail) × overdue_factor + band_cost_factor`

- `w等级考`：config 标记的等级考科目 ×1.5，统考科 ×1.0，合格考轻量科 ×0.5。
- `overdue_factor`：逾期天数/7 封顶 2.0。
- `band_cost_factor`：错题卡 band 轴掉级成本 ≥3分 的 +0.5。
- escalated 恒置顶（不参与加权竞争）。
- 清单默认 ≤8 条/天（学生时间成本红线），超出部分滚动到明天并在报告注明积压量。

## 4. 记忆型间隔档（英语词汇/语法、语文背诵类，K层错因）

首排 1 天；pass 后 2/4/8/16 天（更短更密的艾宾浩斯型），达 mastered 阈值同双条件规则但
关键点判定放宽为"要点清单覆盖≥80%"。

## 5. 考前模式（--exam-mode，对接 grading-policy 时间窗）

- 距该科考试 ≤21 天触发建议：合并 archived 唤醒项 + escalated + 近30天新错，按"错题簇
  边际收益"排序（簇权重公式见 grading-policy §2），生成考前错题簇清单而非日常间隔流。
- 等级考仅一次机会：报告须明示"距高三5月等级考还有 N 天"（按 config 年级推算，标"预估"）。

## 6. 防污染硬规则

- AI 代做/演示产生的"错误"（如教学时举例答错）记 `origin=demo`，永不进掌握统计。
- 判分由 AI 做出且置信=low 时禁发 pass；只可 pending_confirmation。
- 学生自报"这题其实会，当时失误"：记 `watch` 型，跳过排期，考前簇仍会出现一次（防自我误判）。
