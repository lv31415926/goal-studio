"""Layer 2: 时间窗口对齐

维护 (voice, mouse, selection) 元组的滑动窗口,200ms 粒度对齐。
窗口大小 2s,过期信号自动清理。
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from goal_studio.intent_parser.modality_ingestion import Modality, ModalitySignal


@dataclass
class AlignedSignal:
    """对齐后的信号三元组"""
    voice: ModalitySignal | None = None
    text: ModalitySignal | None = None
    mouse: ModalitySignal | None = None
    selection: str | None = None  # 当前选中节点 ID
    hover: str | None = None      # 当前悬停节点 ID
    timestamp: float = 0.0

    @property
    def primary_text(self) -> str:
        """主要文本内容(优先 voice,其次 text)"""
        if self.voice and self.voice.content:
            return self.voice.content
        if self.text and self.text.content:
            return self.text.content
        return ""


class SignalBuffer:
    """Layer 2: 信号缓冲区

    200ms 粒度对齐,2s 滑动窗口。
    """

    WINDOW_MS = 2000
    ALIGN_MS = 200

    def __init__(self) -> None:
        self._signals: deque[ModalitySignal] = deque(maxlen=100)
        self._current_selection: str | None = None
        self._current_hover: str | None = None
        # R1-16: 串行化 push/align/recent_nodes,保护 deque 与状态字段并发访问
        self._lock = threading.Lock()

    def push(self, signal: ModalitySignal) -> None:
        """添加信号"""
        # R1-16: 加锁保护并发写入
        with self._lock:
            # 跟踪 selection/hover 状态
            if signal.modality == Modality.MOUSE:
                node_id = signal.metadata.get("node_id")
                if signal.content == "select" and node_id:
                    self._current_selection = node_id
                elif signal.content == "hover" and node_id:
                    self._current_hover = node_id
                elif signal.content == "unhover":
                    self._current_hover = None

            self._signals.append(signal)
            self._expire()

    def _expire(self) -> None:
        """清理过期信号(超过窗口)"""
        import time

        cutoff = time.time() - self.WINDOW_MS / 1000
        while self._signals and self._signals[0].timestamp < cutoff:
            self._signals.popleft()

    def align(self) -> AlignedSignal:
        """对齐当前窗口内的信号"""
        # R1-16: 加锁保护并发读取
        with self._lock:
            self._expire()
            aligned = AlignedSignal(
                selection=self._current_selection,
                hover=self._current_hover,
                timestamp=self._signals[-1].timestamp if self._signals else 0,
            )
            # 取最新的 voice 和 text
            for sig in reversed(self._signals):
                if sig.modality == Modality.VOICE and aligned.voice is None:
                    aligned.voice = sig
                elif sig.modality == Modality.TEXT and aligned.text is None:
                    aligned.text = sig
                elif sig.modality == Modality.MOUSE and aligned.mouse is None:
                    aligned.mouse = sig
                if aligned.voice and aligned.text and aligned.mouse:
                    break
            return aligned

    @property
    def recent_nodes(self) -> list[str]:
        """最近交互过的节点 ID 列表(按时间倒序)"""
        # R1-16: 加锁保护并发读取
        with self._lock:
            nodes: list[str] = []
            seen: set[str] = set()
            for sig in reversed(self._signals):
                nid = sig.metadata.get("node_id")
                if nid and nid not in seen:
                    nodes.append(nid)
                    seen.add(nid)
            return nodes

    def reset(self) -> None:
        """重置缓冲区"""
        self._signals.clear()
        self._current_selection = None
        self._current_hover = None
