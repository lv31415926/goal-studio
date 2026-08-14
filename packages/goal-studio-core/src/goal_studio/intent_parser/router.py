"""模态路由决策树

根据用户输入和画布状态决定使用哪种模态组合。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from goal_studio.intent_parser.signal_buffer import AlignedSignal


class ModalityRoute(str, Enum):
    """模态路由结果"""
    TEXT = "text"                              # 直接文本命令
    MOUSE_PRIMARY_VOICE_CONFIRM = "mouse_voice"  # 鼠标主+语音确认
    VOICE_CANVAS_HIGHLIGHT = "voice_highlight"   # 语音主+画布高亮
    SELECTION_PRIMARY = "selection"              # 当前选中
    HOVER_PRIMARY = "hover"                      # 当前悬停
    CLARIFY = "clarify"                          # 需要澄清
    VOICE_VISUAL_FILTER = "voice_filter"         # 语音+视觉过滤
    VOICE_PRIMARY = "voice"                      # 语音主
    VOICE_NUMBER_OVERLAY = "voice_numbers"       # 语音+编号(移动端/无障碍)


class ModalityRouter:
    """模态路由器"""

    def route(
        self,
        text: str,
        signal: AlignedSignal,
        is_mobile: bool = False,
        accessibility_mode: bool = False,
    ) -> ModalityRoute:
        """路由决策

        Args:
            text: 用户输入文本
            signal: 对齐后的信号
            is_mobile: 是否移动端
            accessibility_mode: 是否无障碍模式

        Returns:
            ModalityRoute
        """
        if is_mobile or accessibility_mode:
            return ModalityRoute.VOICE_NUMBER_OVERLAY

        text_lower = text.lower().strip()
        has_selection = signal.selection is not None
        has_hover = signal.hover is not None
        mouse_recent = signal.mouse is not None and signal.mouse.age_ms < 2000

        # 显式 ID/name
        if any(text_lower.startswith(p) for p in ["node-", "n-", "l1-", "l2-", "l3-"]):
            return ModalityRoute.TEXT

        # 空间语言
        spatial_kws = ["左边", "右边", "上面", "下面", "左侧", "右侧",
                       "left", "right", "above", "below", "top", "bottom"]
        if any(kw in text_lower for kw in spatial_kws):
            if mouse_recent:
                return ModalityRoute.MOUSE_PRIMARY_VOICE_CONFIRM
            return ModalityRoute.VOICE_CANVAS_HIGHLIGHT

        # 代词
        pronoun_kws = ["这个", "那个", "this", "that", "it"]
        if any(kw in text_lower for kw in pronoun_kws):
            if has_selection:
                return ModalityRoute.SELECTION_PRIMARY
            if has_hover:
                return ModalityRoute.HOVER_PRIMARY
            return ModalityRoute.CLARIFY

        # 属性描述
        property_kws = ["红色", "蓝色", "高优先级", "低优先级", "red", "blue",
                        "high priority", "low priority"]
        if any(kw in text_lower for kw in property_kws):
            return ModalityRoute.VOICE_VISUAL_FILTER

        # 创建类
        create_kws = ["创建", "新建", "添加", "增加", "create", "add", "new"]
        if any(kw in text_lower for kw in create_kws):
            return ModalityRoute.VOICE_PRIMARY

        # 参数编辑
        param_kws = ["改成", "修改", "设置", "改为", "change", "set", "update"]
        if any(kw in text_lower for kw in param_kws):
            return ModalityRoute.VOICE_PRIMARY

        # 默认
        return ModalityRoute.VOICE_PRIMARY


_ = Any
