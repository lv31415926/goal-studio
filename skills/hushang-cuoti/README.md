# 沪上错题 · 上海高中错题全生命周期管理技能

面向上海高中生（3+3 选科/等级赋分语境）的 AI Agent 技能：拍照即录、归因到沪教版知识点、
按记忆规律排期复习、关键点匹配销号、连错升级出变式、考点簇定期总结、四段式深度报告（含原题）。

## 环境要求
- DSH Desktop / MaxFun 系 Agent 技能宿主（技能形态为 Markdown 指令 + 一个纯标准库 Python 脚本）
- Python 3.8+（`scripts/cuoti.py` 零第三方依赖）
- 宿主会话具备多模态识图能力（识别/归因/判分/变式/复核全部由**当前会话所选模型**完成，
  不固化模型供应商、不需要单独 API key）

## 安装
```bash
bash install.sh            # 默认装到 ~/.agents/skills/沪上错题/
# 或手工：cp -R 沪上错题 ~/.agents/skills/ && python3 ~/.agents/skills/沪上错题/scripts/cuoti.py init
```

## 使用（6 个动词）
| 说 | 做 |
|---|---|
| 「记错题」+ 错题照片 | 识别→归因→查重→入库→排期 |
| 「这整卷分析一遍」+ 试卷/答题纸照片 | 整卷匹配→逐错题独立四段式分析→合并 PDF |
| 「今日复习」 | 到期清单（遮答案重做） |
| 「判一下」+ 重做草稿 | 结果+关键点双条件判分，连对销号/连错升级 |
| 「给 wa-xxx 出变式」 | 同考点变式（对外输出过独立复核门） |
| 「周报 / 簇总结 / 一模报告」 | 长图卡片 / 考点簇深潜 / 考前错题簇 |
| 「设置：高一，选科物化政」 | 学期×册次矩阵、等级考权重激活 |

数据全部本地：`~/.hushang-cuoti/`（JSONL append-only，可用 `CUOTI_HOME` 重定向）。
知识系统三层基准：国家课程标准(2017版2020修订) → 2026 在用教材章目录 → 当年考试文件；
政策口径带年份，每年对技能说「更新政策」即触发核对。

## 从 GitHub 一键安装
```bash
curl -L https://github.com/lv31415926/goal-studio/archive/refs/heads/main.tar.gz | tar xz -C /tmp
bash /tmp/goal-studio-main/skills/hushang-cuoti/install.sh
```
许可：随本仓库整体适用 Apache-2.0（见根目录 LICENSE）。
