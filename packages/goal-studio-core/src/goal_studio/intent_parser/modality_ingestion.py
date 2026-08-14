"""Layer 1: 多模态信号采集

统一语音/文本/鼠标三路输入为 ModalitySignal 流。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Modality(str, Enum):
    VOICE = "voice"
    TEXT = "text"
    MOUSE = "mouse"


@dataclass
class ModalitySignal:
    """单模态信号"""
    modality: Modality
    content: str
    """文本内容(语音转录/文本输入/鼠标动作描述)"""

    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    """时间戳(秒)"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """额外信息: 鼠标坐标/选中节点ID/ASR置信度等"""

    @property
    def age_ms(self) -> float:
        """信号年龄(毫秒)"""
        return (datetime.now().timestamp() - self.timestamp) * 1000


class ModalityIngestion:
    """Layer 1: 多模态信号采集器

    接收原始输入,输出统一的 ModalitySignal。
    """

    def __init__(self) -> None:
        self._pending: list[ModalitySignal] = []

    def ingest_voice(self, transcript: str, confidence: float = 0.9) -> ModalitySignal:
        """接收语音转录"""
        sig = ModalitySignal(
            modality=Modality.VOICE,
            content=transcript,
            metadata={"asr_confidence": confidence},
        )
        self._pending.append(sig)
        return sig

    def ingest_text(self, text: str) -> ModalitySignal:
        """接收文本输入"""
        sig = ModalitySignal(modality=Modality.TEXT, content=text)
        self._pending.append(sig)
        return sig

    def ingest_mouse(
        self,
        action: str,
        node_id: str | None = None,
        position: tuple[float, float] | None = None,
    ) -> ModalitySignal:
        """接收鼠标事件

        Args:
            action: hover/click/drag/select
            node_id: 目标节点 ID
            position: (x, y) 坐标
        """
        sig = ModalitySignal(
            modality=Modality.MOUSE,
            content=action,
            metadata={
                "node_id": node_id,
                "position": position,
            },
        )
        self._pending.append(sig)
        return sig

    def drain(self) -> list[ModalitySignal]:
        """取出并清空所有待处理信号"""
        signals = self._pending
        self._pending = []
        return signals

    def peek_latest(self, modality: Modality | None = None) -> ModalitySignal | None:
        """查看最新信号(不消费)"""
        if not self._pending:
            return None
        if modality is None:
            return self._pending[-1]
        for sig in reversed(self._pending):
            if sig.modality == modality:
                return sig
        return None
