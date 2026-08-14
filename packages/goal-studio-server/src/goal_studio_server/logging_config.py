"""集中式日志配置 — 结构化 JSON 输出

P-LOW-2-9: 为全仓库引入可观测性,关键路径加结构化日志。
JsonFormatter 输出单行 JSON,便于 ELK/Loki 采集,业务字段通过 extra 传入。
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
from datetime import UTC, datetime

# 排除 LogRecord 内置字段,只透传业务字段
_RESERVED_KEYS = frozenset({
    "args", "msg", "levelno", "pathname", "filename", "module", "exc_info",
    "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process",
    "name", "levelname", "message", "taskName",
})


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志格式器

    业务字段通过 logger.info("msg", extra={"session_id": ...}) 传入,
    自动透传到 JSON 输出。异常信息放在 "exc" 字段。
    """

    def format(self, record: logging.LogRecord) -> str:
        log: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 透传业务字段
        for k, v in record.__dict__.items():
            if k in _RESERVED_KEYS:
                continue
            if k.startswith("_"):
                continue
            log[k] = v
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        return json.dumps(log, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None) -> None:
    """启动时调用一次,配置 goal_studio / goal_studio_server 两个 logger

    Args:
        level: 日志级别,默认读 GOAL_STUDIO_LOG_LEVEL 环境变量,再默认 INFO
    """
    lvl = level or os.getenv("GOAL_STUDIO_LOG_LEVEL", "INFO")
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stderr,
                "formatter": "json",
            }
        },
        "loggers": {
            "goal_studio": {
                "level": lvl,
                "handlers": ["console"],
                "propagate": False,
            },
            "goal_studio_server": {
                "level": lvl,
                "handlers": ["console"],
                "propagate": False,
            },
        },
        "root": {"level": "WARNING", "handlers": ["console"]},
    }
    logging.config.dictConfig(config)
