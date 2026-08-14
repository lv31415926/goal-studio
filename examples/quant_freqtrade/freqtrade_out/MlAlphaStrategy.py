"""MlAlphaStrategy - goal-studio 量化工作流生成的策略骨架

由 workflow_to_freqtrade.py 自动生成, 对应工作流节点:
  - 因子特征工程 (l1-3): populate_indicators 计算 EMA/RSI
  - 策略信号与回测 (l1-4): populate_entry_trend / populate_exit_trend
  - 参数优化与风控 (l1-5): stoploss / trailing_stop
  - 多策略组合 (l1-8): 本文件为 ml_alpha 子策略
"""
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class MlAlphaStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    stoploss = -0.10
    trailing_stop = True
    minimal_roi = {"0": 0.04, "30": 0.02, "60": 0.01}

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
