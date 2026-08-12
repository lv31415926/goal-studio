# 示例: 用 goal-studio 开发 API

```bash
goal-studio init --name my-api
cd my-api

goal-studio draft \
  --goal "开发一个用户登录 API,支持邮箱/手机号登录" \
  --type code \
  --constraint "使用 FastAPI" \
  --criteria "通过 pytest 测试"
```

Phase 2 实现 CodeAdapter 后可用。
