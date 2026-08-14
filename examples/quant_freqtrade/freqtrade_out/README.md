# freqtrade 配置骨架 (goal-studio 量化工作流导出)

由 `workflow_to_freqtrade.py` 从会话 `53ab45399634` 生成 (graph v6, 53 节点)。

## 节点 -> 配置映射

| 工作流节点 | freqtrade 配置项 |
| --- | --- |
| l3-9.1.1 券商API接入 | `exchange.name` = binance, rate_limit |
| l3-9.1.2 账户安全配置 | 密钥走环境变量, 不落盘明文 |
| l3-9.2.1 订单执行配置 | `order_types` = limit, unfilledtimeout |
| l3-5.2.1 仓位控制 | `tradable_balance_ratio` = 0.8, stoploss |
| l3-6.1.1 模拟盘运行 | `dry_run` = True |
| l3-8.1.1 策略加权配置 | 多策略组合说明 (多实例编排) |
| l3-7.2.1 再训练调度 | `freqai` 段 (周期重训) |

## 使用

```bash
# 1. 设置密钥环境变量 (勿写入配置文件)
export FREQTRADE_API_KEY=xxx FREQTRADE_API_SECRET=xxx

# 2. 复制策略到 user_data/strategies/
cp *Strategy.py user_data/strategies/

# 3. 先跑模拟盘验证 (dry_run=true)
freqtrade trade --config config.json --strategy <StrategyName>
```

> 本骨架仅供研发/模拟盘验证, 实盘前必须通过模拟盘考核 (偏差<5%) 并人工复核风控参数。
