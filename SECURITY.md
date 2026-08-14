# 安全政策 (Security Policy)

## 支持的版本 (Supported Versions)

| 版本 | 支持状态 |
|------|----------|
| 0.1.x | ✅ 积极维护 |

## 报告漏洞 (Reporting a Vulnerability)

请**不要**公开披露安全漏洞。请通过以下方式私下报告:

- 在 GitHub 仓库创建 **Private Security Advisory** (推荐)
- 或发送邮件至维护者(见仓库简介)

请提供:
1. 受影响的版本与端点
2. 复现步骤 (PoC 优先)
3. 影响评估

我们会:
- 在 48 小时内确认收到
- 评估严重性并制定修复计划
- 修复后发布安全公告

## 安全设计 (Security Design)

### 认证模型
- **开发模式 (dev)**: 默认关闭认证,所有请求放行(仅限本地)
- **生产模式 (production)**: 必须设置 `GOAL_STUDIO_API_TOKEN`,否则所有请求被拒绝 (fail-closed)
- Token 通过 `Authorization: Bearer <token>` 或 `?token=` 传递,使用 `hmac.compare_digest` 常时比较

### 传输安全
- 生产部署必须启用 HTTPS (建议反向代理如 Nginx/Caddy 终止 TLS)
- WebSocket 在 TLS 下使用 WSS

### 关键环境变量
| 变量 | 说明 | 生产要求 |
|------|------|----------|
| `GOAL_STUDIO_ENV` | `dev` / `production` | 必须为 `production` |
| `GOAL_STUDIO_API_TOKEN` | API Token | 必须设置 (强随机值) |
| `GOAL_STUDIO_CORS_ORIGINS` | 允许的 Origin 白名单 | 必须显式配置 |
| `GOAL_STUDIO_SESSION_ROOT` | 会话存储目录 | 建议独立卷 |

### 防御机制
- **路径遍历防护**: `session_id` / `artifact_id` / `version` 均通过白名单正则校验
- **WebSocket Origin 校验**: 生产模式拒绝非白名单 Origin (防 CSWSH)
- **连接限制**: 每会话最多 3 个 WS 连接,全局最多 100 个
- **速率限制**: 每 WS 连接每秒最多 10 条消息
- **消息大小限制**: 64KB
- **异常脱敏**: 全局异常处理器不向客户端泄露内部细节
- **文件权限**: 会话/产物文件权限为 0600
- **Metrics 鉴权**: `/metrics` 端点需要认证(生产模式)

### 报告处理流程
1. 确认漏洞真实性
2. 评估影响范围与严重性 (CVSS)
3. 开发修复补丁
4. 内部验证 + 回归测试
5. 发布修复版本并公告
