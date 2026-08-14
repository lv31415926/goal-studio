"""workflow -> freqtrade 配置骨架转换器

把 goal-studio 量化工作流会话 (含 L3 slots) 转换为 freqtrade 可运行的配置骨架:
  - config.json               主配置 (exchange/dry_run/风控/telegram/freqai 再训练)
  - <Strategy>Strategy.py     策略骨架 (populate_indicators/entry/exit)
  - README.md                 节点 -> 配置项映射说明

用法:
  python workflow_to_freqtrade.py <session.json> [--workflow workflow.json] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# L3 节点 ID -> 语义 (与量化工作流 DAG 对应)
NODE_KEYS = {
    "broker_api": "l3-9.1.1",
    "account_security": "l3-9.1.2",
    "order_exec": "l3-9.2.1",
    "position_ctrl": "l3-5.2.1",
    "dryrun": "l3-6.1.1",
    "strategy_mix": "l3-8.1.1",
    "retrain": "l3-7.2.1",
}


def load_graph(session_path: Path) -> dict:
    data = json.loads(session_path.read_text(encoding="utf-8"))
    graph = data.get("graph")
    if not graph:
        raise SystemExit(f"会话无 graph: {session_path}")
    return graph


def slots_of(graph: dict, node_id: str) -> dict:
    node = graph.get("nodes", {}).get(node_id)
    return (node or {}).get("slots", {}) or {}


def build_config(graph: dict) -> dict:
    broker = slots_of(graph, NODE_KEYS["broker_api"])
    order = slots_of(graph, NODE_KEYS["order_exec"])
    pos = slots_of(graph, NODE_KEYS["position_ctrl"])
    dryrun = slots_of(graph, NODE_KEYS["dryrun"])
    mix = slots_of(graph, NODE_KEYS["strategy_mix"])
    retrain = slots_of(graph, NODE_KEYS["retrain"])

    # dry_run: 模拟盘节点存在即默认开启, 实盘需显式关闭
    dry_run = bool(dryrun)

    config = {
        "max_open_trades": max(1, int(float(pos.get("max_total_position", 0.8)) * 10)),
        "stake_currency": "USDT",
        "stake_amount": "unlimited",
        "tradable_balance_ratio": float(pos.get("max_total_position", 0.8)),
        "dry_run": dry_run,
        "dry_run_wallet": 1000,
        "cancel_open_orders_on_exit": False,
        "trading_mode": "spot",
        "margin_mode": "",
        "timeframe": "1h",
        "exchange": {
            "name": broker.get("exchange", "binance"),
            "key": "${FREQTRADE_API_KEY}",
            "secret": "${FREQTRADE_API_SECRET}",
            "ccxt_config": {},
            "ccxt_async_config": {},
            "pair_whitelist": ["BTC/USDT", "ETH/USDT"],
            "pair_blacklist": [],
        },
        "order_types": {
            "entry": order.get("order_type", "limit"),
            "exit": order.get("order_type", "limit"),
            "stoploss": "market",
            "stoploss_on_exchange": False,
        },
        "unfilledtimeout": {"entry": 10, "exit": 10, "unit": "minutes"},
        "stoploss": -0.10,
        "trailing_stop": True,
        "telegram": {
            "enabled": True,
            "token": "${FREQTRADE_TELEGRAM_TOKEN}",
            "chat_id": "${FREQTRADE_TELEGRAM_CHAT_ID}",
        },
        "api_server": {
            "enabled": True,
            "listen_ip_address": "127.0.0.1",
            "listen_port": 8080,
            "verbosity": "error",
            "username": "freqtrader",
            "password": "${FREQTRADE_API_PASSWORD}",
        },
        "bot_name": "goal-studio-quant-workflow",
        "initial_state": "running",
        "force_entry_enable": False,
        "internals": {"process_throttle_secs": 5},
    }

    # freqai 再训练段 (来自 l3-7.2.1 再训练调度, moving-window 周期重训)
    if retrain:
        config["freqai"] = {
            "enabled": True,
            "kickoff_time": "2026-01-01",
            "data_split_parameters": {"test_days": 7},
            "feature_parameters": {
                "include_timeframes": ["1h"],
                "include_corr_pairlist": [],
                "label_period_candle": 24,
            },
            "model_training_parameters": {},
        }

    # 多策略组合: freqtrade 单 config 单策略, 组合需多实例 + 外部编排按权重分配资金
    if mix:
        config["_strategy_mix_note"] = {
            "method": mix.get("method", "risk_parity"),
            "strategies": mix.get("strategies", []),
            "rebalance": mix.get("rebalance", "weekly"),
            "note": "多策略组合: 每个子策略独立 freqtrade 实例运行, 由外部编排层按权重分配资金",
        }
    return config


STRATEGY_TEMPLATE = '''"""{class_name} - goal-studio 量化工作流生成的策略骨架

由 workflow_to_freqtrade.py 自动生成, 对应工作流节点:
  - 因子特征工程 (l1-3): populate_indicators 计算 EMA/RSI
  - 策略信号与回测 (l1-4): populate_entry_trend / populate_exit_trend
  - 参数优化与风控 (l1-5): stoploss / trailing_stop
  - 多策略组合 (l1-8): 本文件为 {strategy_name} 子策略
"""
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class {class_name}(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    stoploss = -0.10
    trailing_stop = True
    minimal_roi = {{"0": 0.04, "30": 0.02, "60": 0.01}}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["rsi"] < 70)
            & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["ema20"] < dataframe["ema50"]), "exit_long"] = 1
        return dataframe
'''

README_TEMPLATE = """# freqtrade 配置骨架 (goal-studio 量化工作流导出)

由 `workflow_to_freqtrade.py` 从会话 `{session_id}` 生成 (graph v{version}, {node_count} 节点)。

## 节点 -> 配置映射

| 工作流节点 | freqtrade 配置项 |
| --- | --- |
| l3-9.1.1 券商API接入 | `exchange.name` = {exchange}, rate_limit |
| l3-9.1.2 账户安全配置 | 密钥走环境变量, 不落盘明文 |
| l3-9.2.1 订单执行配置 | `order_types` = {order_type}, unfilledtimeout |
| l3-5.2.1 仓位控制 | `tradable_balance_ratio` = {ratio}, stoploss |
| l3-6.1.1 模拟盘运行 | `dry_run` = {dry_run} |
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
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="workflow -> freqtrade 配置骨架")
    ap.add_argument("session", help="goal-studio 会话 JSON 路径")
    ap.add_argument("--workflow", default="", help="workflow.json 路径 (可选, 暂未使用)")
    ap.add_argument("--out", default="./freqtrade_out", help="输出目录")
    args = ap.parse_args()

    session_path = Path(args.session)
    graph = load_graph(session_path)
    config = build_config(graph)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    strategies = slots_of(graph, NODE_KEYS["strategy_mix"]).get("strategies") or ["quant_workflow"]
    for name in strategies:
        class_name = "".join(w.capitalize() for w in str(name).split("_")) + "Strategy"
        (out / f"{class_name}.py").write_text(
            STRATEGY_TEMPLATE.format(class_name=class_name, strategy_name=name),
            encoding="utf-8",
        )

    broker = slots_of(graph, NODE_KEYS["broker_api"])
    order = slots_of(graph, NODE_KEYS["order_exec"])
    pos = slots_of(graph, NODE_KEYS["position_ctrl"])
    session_data = json.loads(session_path.read_text(encoding="utf-8"))
    (out / "README.md").write_text(
        README_TEMPLATE.format(
            session_id=session_data.get("id", "?"),
            version=graph.get("version", "?"),
            node_count=len(graph.get("nodes", {})),
            exchange=broker.get("exchange", "binance"),
            order_type=order.get("order_type", "limit"),
            ratio=pos.get("max_total_position", 0.8),
            dry_run=config["dry_run"],
        ),
        encoding="utf-8",
    )
    print(f"freqtrade 骨架已生成: {out}")
    print(f"  - config.json (dry_run={config['dry_run']}, exchange={config['exchange']['name']})")
    print(f"  - {len(strategies)} 个策略骨架")
    print("  - README.md")


if __name__ == "__main__":
    main()
