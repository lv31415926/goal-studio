# 生产部署

本文档说明如何将 goal-studio 部署到生产环境。

## 部署方式

### 方式一: Docker Compose (推荐)

```bash
# 1. 准备环境变量
cp .env.example .env
# 编辑 .env,至少设置:
#   GOAL_STUDIO_ENV=production
#   GOAL_STUDIO_API_TOKEN=<强随机值>
#   GOAL_STUDIO_CORS_ORIGINS=https://your-domain.com

# 2. 构建并启动
docker compose up -d --build

# 3. 验证
curl -H "Authorization: Bearer $GOAL_STUDIO_API_TOKEN" http://localhost:8000/api/health
```

### 方式二: 直接运行 (Python + 前端构建)

```bash
# 1. 构建前端
cd packages/goal-studio-web
pnpm install && pnpm run build:app

# 2. 安装后端
cd ../goal-studio-server
pip install -e ../goal-studio-core
pip install -e .

# 3. 配置环境变量
export GOAL_STUDIO_ENV=production
export GOAL_STUDIO_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export GOAL_STUDIO_CORS_ORIGINS="https://your-domain.com"

# 4. 启动
uvicorn goal_studio_server.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1

# 注意: 当前版本仅支持单 worker 模式(内存中的速率限制桶、WS 连接状态、PipelineCache 
# 均为进程内状态)。多 worker 需先实现 Redis 共享状态层。使用 --workers 1 或不指定 workers。
```

::: warning 反向代理
生产环境必须在前方部署反向代理 (Nginx / Caddy) 终止 TLS。WebSocket 升级需在代理中显式支持:

```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```
:::

## 环境变量清单

| 变量 | 必填 | 说明 |
|------|------|------|
| `GOAL_STUDIO_ENV` | ✅ | 必须为 `production` |
| `GOAL_STUDIO_API_TOKEN` | ✅ | API 认证 Token (强随机值) |
| `GOAL_STUDIO_CORS_ORIGINS` | ✅ | 允许的 Origin 白名单(逗号分隔) |
| `GOAL_STUDIO_SESSION_ROOT` | 可选 | 会话存储目录(默认 `~/.goal-studio/sessions`) |
| `GOAL_STUDIO_ARTIFACT_ROOT` | 可选 | 产物存储目录(默认 `./artifacts`) |
| `GOAL_STUDIO_WEB_DIST` | 可选 | 前端构建产物目录(Docker 中自动配置) |
| `GOAL_STUDIO_LLM_PROVIDER` | 可选 | `mock` / `openai` / `anthropic`(默认 mock) |
| `OPENAI_API_KEY` | 视情况 | 使用 OpenAI/兼容 API 时必填 |
| `GOAL_STUDIO_METRICS_TOKEN` | 可选 | 独立 Metrics 访问 Token(默认复用 API Token) |

## 安全清单

- [x] `GOAL_STUDIO_ENV=production`(强制认证, fail-closed)
- [x] `GOAL_STUDIO_API_TOKEN` 已设置为强随机值
- [x] `GOAL_STUDIO_CORS_ORIGINS` 仅包含可信来源
- [x] 启用 HTTPS + 反向代理
- [x] `/metrics` 端点受鉴权保护
- [x] 数据卷权限收紧(会话/产物目录非 root 可读写)

## 监控与健康检查

### 健康检查

```bash
# 基础健康检查(无需鉴权,用于负载均衡/编排器探活)
curl http://localhost:8000/api/health

# 详细健康检查(需鉴权)
curl -H "Authorization: Bearer $GOAL_STUDIO_API_TOKEN" \
     http://localhost:8000/api/health/detail
```

### Metrics (Prometheus 格式)

```bash
curl -H "Authorization: Bearer $GOAL_STUDIO_METRICS_TOKEN" \
     http://localhost:8000/metrics
```

暴露指标:
- `http_requests_total{endpoint}` — 各端点请求计数
- `http_request_duration_seconds{endpoint}` — 平均延迟
- `ws_connections_active` — 活跃 WS 连接数
- `ws_messages_total` — WS 消息总数
- `llm_calls_total` / `llm_errors_total` — LLM 调用与错误计数

### Docker Compose 资源限制

```yaml
deploy:
  resources:
    limits:
      cpus: "1"
      memory: 512M
```

## 数据持久化

| 数据 | 默认位置 | 建议 |
|------|----------|------|
| 会话 | `~/.goal-studio/sessions` | 独立数据卷 `/data/sessions` |
| 产物 | `./artifacts` | 独立数据卷 `/data/artifacts` |

## 监控与告警

### Prometheus 抓取配置

在 `prometheus.yml` 中添加 goal-studio 的 scrape job:

```yaml
scrape_configs:
  - job_name: goal-studio
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: /metrics
    scheme: http
    bearer_token: <GOAL_STUDIO_METRICS_TOKEN>
    static_configs:
      - targets:
          - 127.0.0.1:8000
        labels:
          service: goal-studio
          env: production
```

### 告警阈值建议

基于暴露的指标,推荐以下告警规则(供 Alertmanager / Grafana 参考):

| 告警名称 | 触发条件 | 严重级别 | 说明 |
|----------|----------|----------|------|
| `GoalStudioDown` | `up{job="goal-studio"} == 0` 持续 1m | Critical | 服务不可达 |
| `HighErrorRate` | `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05` 持续 5m | High | 5xx 错误率超 5% |
| `HighLatencyP95` | `http_request_duration_seconds > 2` 持续 5m | Warning | 平均延迟超 2s |
| `WSConnectionsDropped` | `ws_connections_active < 1` 持续 10m(非维护窗口) | Warning | WS 连接异常归零 |
| `LLMErrorSpike` | `rate(llm_errors_total[5m]) > 0.1` 持续 5m | High | LLM 调用错误激增 |
| `HealthCheckFail` | `probe_success{job="blackbox-goal-studio"} == 0` 持续 2m | Critical | 健康检查端点失败 |

## 数据备份与恢复

### 数据卷备份

会话与产物数据存储在 Docker 命名卷 `session-data` 与 `artifact-data` 中,建议每日备份:

```bash
# 备份会话数据卷
docker run --rm -v session-data:/data:ro -v $(pwd)/backups:/backup alpine     tar czf /backup/sessions-$(date +%Y%m%d).tar.gz -C /data .

# 备份产物数据卷
docker run --rm -v artifact-data:/data:ro -v $(pwd)/backups:/backup alpine     tar czf /backup/artifacts-$(date +%Y%m%d).tar.gz -C /data .
```

### 数据恢复

```bash
# 恢复会话数据卷(先停止服务)
docker compose stop goal-studio
docker run --rm -v session-data:/data -v $(pwd)/backups:/backup alpine     sh -c "rm -rf /data/* && tar xzf /backup/sessions-20260813.tar.gz -C /data"
docker compose start goal-studio
```

### 自动化备份(Cron)

```bash
# 每日凌晨 3 点备份,保留最近 14 天
0 3 * * * /opt/goal-studio/scripts/backup.sh && find /opt/goal-studio/backups -mtime +14 -delete
```

## 版本回滚

### 镜像 Tag 回退

生产环境应使用显式版本 tag(如 `goal-studio:0.1.0`)而非 `latest`。回滚步骤:

```bash
# 1. 查看当前运行版本
docker inspect goal-studio --format '{{.Config.Image}}'

# 2. 回退到上一版本镜像(假设已构建/拉取)
export PREV_TAG=0.0.9
docker compose down
# 修改 docker-compose.yml 中 image tag 或使用环境变量
docker compose up -d

# 3. 验证回滚后服务正常
curl -H "Authorization: Bearer $GOAL_STUDIO_API_TOKEN" http://localhost:8000/api/health/detail
```

### 数据卷快照回滚

若新版本产生不兼容的数据变更,需配合数据卷快照回滚:

```bash
# 1. 升级前先创建数据卷快照(备份)
docker run --rm -v session-data:/data:ro -v $(pwd)/backups:/backup alpine     tar czf /backup/sessions-pre-upgrade-$(date +%Y%m%d%H%M).tar.gz -C /data .

# 2. 回滚时恢复升级前快照
docker compose stop goal-studio
docker run --rm -v session-data:/data -v $(pwd)/backups:/backup alpine     sh -c "rm -rf /data/* && tar xzf /backup/sessions-pre-upgrade-202608130300.tar.gz -C /data"
docker compose start goal-studio
```

::: warning 回滚注意事项
- 跨大版本回滚可能存在数据迁移脚本不可逆问题,升级前务必完成数据卷备份
- 回滚后建议在维护窗口内执行完整的功能验证
- 若使用外部 LLM Provider,确认 API 兼容性
:::

