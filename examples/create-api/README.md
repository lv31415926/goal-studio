# 示例: 用 goal-studio 开发用户登录 API

完整演示 CodeAdapter 全生命周期: draft → refine → validate → build → export

## 运行

```bash
cd goal-studio  # 项目根目录
python3 examples/create-api/run.py
```

## 流程说明

1. **定义目标契约** — 用户登录 API (邮箱/手机号 + JWT)
2. **起草 DAG** — CodeAdapter.draft() 生成三层 DAG (12 节点)
3. **精化节点** — 给数据模型节点添加字段定义 (email/phone/password_hash)
4. **验证** — 5 维评估 (architecture_clarity / modularity / test_coverage ...)
5. **构建** — 生成 main.py + test_main.py + requirements.txt + README.md
6. **导出** — 持久化到 output/ 目录

## 预期输出

```
examples/create-api/output/
  └── code-<hash>/
      ├── README.md
      ├── src/main.py
      ├── tests/test_main.py
      └── requirements.txt
```

## 也可通过 CLI 运行

```bash
goal-studio init --name my-api && cd my-api
goal-studio draft \
  --goal "开发一个用户登录API,支持邮箱/手机号登录" \
  --type code \
  --constraint "使用FastAPI" \
  --criteria "通过pytest测试"
goal-studio view
goal-studio refine l1-req --patch "定义用户模型字段: email, phone, password_hash"
goal-studio verify
goal-studio build -o ./output
```
