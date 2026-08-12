# 示例: 用 goal-studio 创建技能

## 步骤

```bash
# 1. 初始化项目
goal-studio init --name my-skill-project
cd my-skill-project

# 2. 起草 DAG
goal-studio draft \
  --goal "创建一个日报生成技能,每天早上8点自动汇总昨日工作" \
  --type skill \
  --constraint "必须用中文输出" \
  --criteria "能正确读取昨日 git commit" \
  --criteria "输出 Markdown 格式"

# 3. 查看起草结果
goal-studio view

# 4. 精化节点
goal-studio refine l1-skill-authoring --patch "增加失败重试机制"

# 5. 验证
goal-studio verify

# 6. 固化产物
goal-studio build -o ./output --target "file://./output"
```

## 预期输出

- `output/SKILL.md` - 生成的技能文件
- `sessions/*.json` - 会话历史
- `artifacts/` - 产物版本
