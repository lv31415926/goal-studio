"""Layer 3: LLM 增量解析 (基于 ParsingDST + S3-DST 论文)

把自然语言/语音转录解析为结构化 JSON 中间态 ParsedIntent。
支持:
  - 增量解析: 每收到 ASR chunk 就重新解析(带完整性判定)
  - 状态跟踪: 维护对话上下文栈,支持省略恢复/指代消解
  - 多意图分段: 一句话含多个意图时分段解析 (S3-DST)
  - LRU 缓存: 相同输入不重复调用 LLM
  - 启发式预解析: 规则匹配常见模式,降低 LLM 调用频率
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

import logging

from goal_studio.llm import LLMMessage, get_llm
from goal_studio.llm.base import BaseLLMProvider

_logger = logging.getLogger(__name__)


INTENT_PARSER_SYSTEM_PROMPT = """你是 goal-studio 的意图解析器。把用户的自然语言指令解析为结构化 JSON。

输出严格 JSON:
{
  "intent": "create|modify|delete|connect|query",
  "target_ref": {
    "type": "id|name|spatial|property|pronoun",
    "value": "节点 ID/名称/空间描述/属性描述/代词",
    "confidence": 0.0-1.0
  },
  "slots": {
    "属性名": "新值"
  },
  "ambiguity_flag": false,
  "clarification_options": []
}

规则:
1. intent 判定: 创建/修改/删除/连接/查询
2. target_ref:
   - 用户说节点 ID → type=id
   - 用户说节点名 → type=name
   - 用户说"左边/上面" → type=spatial
   - 用户说"红色/高优先级" → type=property
   - 用户说"这个/那个" → type=pronoun, 若无明确上下文则 ambiguity_flag=true
3. slots: 从指令提取要改的属性
   - "改成异步" → {"execution_mode": "async"}
   - "加重试3次" → {"retry_count": 3}
   - "超时30秒" → {"timeout": 30}
   - "标题改成XXX" → {"title": "XXX"}
4. ambiguity_flag: 当代词无法消歧时设 true,并在 clarification_options 列出候选
5. 只输出 JSON,不要其他文字"""

# 多意图分段符 — 用户一句话含多个指令时的分隔词
INTENT_SEPARATORS = ["然后", "接着", "再", "之后", "并且", "同时", "，", "；", ";", " and ", " then "]

# 启发式规则模式库 (避免 LLM 调用)
HEURISTIC_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "modify_async",
        "pattern": r"(?:把|将)?\s*(l\d+-[\w-]+)\s*(?:改|设|变)(?:成|为)?\s*异步",
        "intent": "modify",
        "ref_type": "id",
        "slots": {"execution_mode": "async"},
    },
    {
        "name": "modify_sync",
        "pattern": r"(?:把|将)?\s*(l\d+-[\w-]+)\s*(?:改|设|变)(?:成|为)?\s*同步",
        "intent": "modify",
        "ref_type": "id",
        "slots": {"execution_mode": "sync"},
    },
    {
        "name": "add_retry",
        "pattern": r"(?:把|将)?\s*(l\d+-[\w-]+)\s*(?:加|设|改)(?:重试|retry)?\s*(\d+)\s*次?",
        "intent": "modify",
        "ref_type": "id",
        "slot_extractor": lambda m: {"retry_count": int(m.group(2))},
    },
    {
        "name": "set_timeout",
        "pattern": r"(?:把|将)?\s*(l\d+-[\w-]+)\s*(?:超时|timeout|时间)(?:设|改成?|为)?\s*(\d+)\s*(?:秒|s)?",
        "intent": "modify",
        "ref_type": "id",
        "slot_extractor": lambda m: {"timeout": int(m.group(2))},
    },
    {
        "name": "modify_title",
        "pattern": r"(?:把|将)?\s*(l\d+-[\w-]+)\s*(?:的)?\s*(?:标题|title)\s*(?:改成|改为|设成|设为|改成|改|设为|改)\s*(.+)",
        "intent": "modify",
        "ref_type": "id",
        "slot_extractor": lambda m: {"title": m.group(2).strip()},
    },
    {
        "name": "delete_node",
        "pattern": r"(?:删除|删掉|移除|去掉)\s*(l\d+-[\w-]+)",
        "intent": "delete",
        "ref_type": "id",
    },
    {
        "name": "create_node",
        "pattern": r"(?:新增|添加|创建|加)\s*(?:一个)?\s*(?:节点|node)\s*(.+)",
        "intent": "create",
        "ref_type": "name",
        "slot_extractor": lambda m: {"title": m.group(1).strip()},
    },
    {
        "name": "connect_nodes",
        "pattern": r"(?:连接|连|接上|connect)\s*(l\d+-[\w-]+)\s*(?:和|到|与|to|→|->)\s*(l\d+-[\w-]+)",
        "intent": "connect",
        "ref_type": "id",
        "slot_extractor": lambda m: {"target_node_id": m.group(2)},
    },
]


@dataclass
class ParsedIntent:
    """解析后的意图(JSON 中间态)"""
    intent: str | None = None
    """create/modify/delete/connect/query"""

    target_ref: dict[str, Any] | None = None
    """{type, value, confidence}"""

    slots: dict[str, Any] = field(default_factory=dict)
    """属性键值对"""

    ambiguity_flag: bool = False
    """是否需要澄清"""

    clarification_options: list[str] = field(default_factory=list)
    """候选节点 ID 列表"""

    raw_text: str = ""
    """原始输入文本"""

    parse_confidence: float = 0.0
    """解析置信度"""

    parse_source: str = "llm"
    """解析来源: llm / heuristic / cache / incremental_partial / fallback"""

    segment_index: int = 0
    """多意图分段时的索引(0=单意图)"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedIntent:
        return cls(
            intent=data.get("intent"),
            target_ref=data.get("target_ref"),
            slots=data.get("slots", {}),
            ambiguity_flag=data.get("ambiguity_flag", False),
            clarification_options=data.get("clarification_options", []),
            raw_text=data.get("raw_text", ""),
            parse_confidence=data.get("parse_confidence", 0.0),
            parse_source=data.get("parse_source", "llm"),
            segment_index=data.get("segment_index", 0),
        )

    @property
    def is_valid(self) -> bool:
        """是否有效(有 intent 且非歧义)"""
        return self.intent is not None and not self.ambiguity_flag


@dataclass
class ContextEntry:
    """上下文栈条目"""
    turn: int
    intent: str | None
    target_ref: dict[str, Any] | None
    slots: dict[str, Any]
    raw_text: str
    timestamp: float
    resolved_node_id: str | None = None
    """Layer 4 消解后的节点 ID(回填)"""


class LRUParseCache:
    """LRU 缓存 — 相同输入不重复调用 LLM

    缓存 key = hash(text + canvas_summary 摘要)
    缓存 value = ParsedIntent
    """

    def __init__(self, capacity: int = 64) -> None:
        self._cache: OrderedDict[str, ParsedIntent] = OrderedDict()
        self._capacity = capacity
        self._hits = 0
        self._misses = 0

    def _make_key(self, text: str, canvas_summary: str) -> str:
        """生成缓存 key"""
        # R2-18: 用完整 canvas_summary 的 sha256 hash 替代前 200 字符截取,
        # 避免超长摘要前 200 字符相同时缓存误命中
        canvas_hash = hashlib.sha256(canvas_summary.encode()).hexdigest()[:16]
        text_hash = hashlib.md5(text.encode()).hexdigest()
        return f"{text_hash}:{canvas_hash}"

    def get(self, text: str, canvas_summary: str) -> ParsedIntent | None:
        key = self._make_key(text, canvas_summary)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, text: str, canvas_summary: str, intent: ParsedIntent) -> None:
        key = self._make_key(text, canvas_summary)
        self._cache[key] = intent
        self._cache.move_to_end(key)
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0


class IncrementalLLMParser:
    """Layer 3: LLM 增量解析器

    基于 ParsingDST 论文,把对话文本→JSON 中间态。
    支持:
      - 增量解析(流式 ASR 每收到 chunk 重新解析)
      - 上下文跟踪(对话状态栈,支持省略恢复)
      - 多意图分段(S3-DST,一句话拆成多个意图)
      - LRU 缓存(相同输入不重复调 LLM)
      - 启发式预解析(规则匹配,跳过 LLM)
    """

    def __init__(
        self,
        llm: BaseLLMProvider | None = None,
        context_capacity: int = 10,
        cache_capacity: int = 64,
    ) -> None:
        self._llm = llm
        self._context: list[ContextEntry] = []
        self._context_capacity = context_capacity
        self._cache = LRUParseCache(cache_capacity)
        self._last_incremental_text: str = ""
        self._last_incremental_time: float = 0.0

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    @property
    def cache(self) -> LRUParseCache:
        return self._cache

    @property
    def context(self) -> list[ContextEntry]:
        return self._context

    def parse(
        self,
        text: str,
        canvas_summary: str = "",
        dialog_history: list[dict[str, str]] | None = None,
    ) -> ParsedIntent:
        """解析文本为 ParsedIntent

        解析顺序: 启发式 → 缓存 → LLM
        支持多意图分段: 若检测到分隔符,返回第一个意图(其余可通过 parse_segments 获取)

        Args:
            text: 用户输入文本
            canvas_summary: 画布状态摘要
            dialog_history: 最近对话历史

        Returns:
            ParsedIntent (segment_index=0 表示第一个分段)
        """
        text = text.strip()
        if not text:
            return ParsedIntent(raw_text="", parse_source="fallback", parse_confidence=0.0)

        # 多意图分段检测
        segments = self._split_segments(text)
        if len(segments) > 1:
            # 解析第一个分段,后续分段存入上下文供连续处理
            first = self._parse_single(segments[0], canvas_summary, dialog_history, segment_index=0)
            # 预解析剩余分段(不调用 LLM,仅标记)
            for i, seg in enumerate(segments[1:], start=1):
                self._context.append(ContextEntry(
                    turn=len(self._context) + 1,
                    intent=None,
                    target_ref=None,
                    slots={},
                    raw_text=seg,
                    timestamp=time.time(),
                ))
            return first

        return self._parse_single(text, canvas_summary, dialog_history, segment_index=0)

    def parse_segments(
        self,
        text: str,
        canvas_summary: str = "",
        dialog_history: list[dict[str, str]] | None = None,
    ) -> list[ParsedIntent]:
        """解析多意图文本 — 返回所有分段

        用于一句话含多个意图的场景:
          "把 l1-req 改成异步,然后删除 l1-design"
          → [ParsedIntent(modify, l1-req), ParsedIntent(delete, l1-design)]
        """
        text = text.strip()
        segments = self._split_segments(text)
        results: list[ParsedIntent] = []
        for i, seg in enumerate(segments):
            intent = self._parse_single(seg, canvas_summary, dialog_history, segment_index=i)
            results.append(intent)
        return results

    def parse_incremental(
        self,
        current_text: str,
        canvas_summary: str = "",
        dialog_history: list[dict[str, str]] | None = None,
    ) -> ParsedIntent:
        """增量解析(流式 ASR 每收到 chunk 调用)

        策略:
          1. 文本不完整 → 返回低置信度预解析(尝试启发式)
          2. 文本完整 → 调用 parse()
          3. 去重: 与上次增量文本相同则返回缓存结果
        """
        text = current_text.strip()
        if not text:
            return ParsedIntent(raw_text="", parse_source="fallback", parse_confidence=0.0)

        # 去重: 相同文本不重复解析
        now = time.time()
        if text == self._last_incremental_text and (now - self._last_incremental_time) < 2.0:
            return ParsedIntent(
                raw_text=text,
                parse_source="incremental_skip",
                parse_confidence=0.0,
                ambiguity_flag=True,
            )
        self._last_incremental_text = text
        self._last_incremental_time = now

        # 完整性检查
        is_complete = self._is_complete(text)
        if not is_complete:
            # 不完整 → 尝试启发式预解析(可能匹配到部分模式)
            heuristic = self._try_heuristic(text)
            if heuristic:
                heuristic.parse_source = "incremental_partial"
                heuristic.parse_confidence *= 0.6  # 降权
                return heuristic
            return ParsedIntent(
                raw_text=text,
                parse_source="incremental_partial",
                parse_confidence=0.2,
                ambiguity_flag=True,
            )

        return self.parse(text, canvas_summary, dialog_history)

    def _parse_single(
        self,
        text: str,
        canvas_summary: str,
        dialog_history: list[dict[str, str]] | None,
        segment_index: int = 0,
    ) -> ParsedIntent:
        """解析单个分段(启发式 → 缓存 → LLM)"""
        # 1. 启发式规则匹配
        heuristic = self._try_heuristic(text)
        if heuristic:
            heuristic.segment_index = segment_index
            self._record_context(heuristic, segment_index)
            return heuristic

        # 2. LRU 缓存
        cached = self._cache.get(text, canvas_summary)
        if cached:
            cached = ParsedIntent.from_dict(cached.to_dict())
            cached.parse_source = "cache"
            cached.segment_index = segment_index
            return cached

        # 3. LLM 解析
        intent = self._llm_parse(text, canvas_summary, dialog_history)
        intent.segment_index = segment_index
        # 写入缓存
        self._cache.put(text, canvas_summary, intent)
        # 记录上下文
        self._record_context(intent, segment_index)
        return intent

    def _try_heuristic(self, text: str) -> ParsedIntent | None:
        """启发式规则匹配 — 命中则跳过 LLM

        匹配 HEURISTIC_PATTERNS 中的模式,返回 ParsedIntent 或 None
        """
        text_lower = text.lower()
        for rule in HEURISTIC_PATTERNS:
            pattern = rule["pattern"]
            # 大小写不敏感匹配
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                continue

            # 提取 target_ref value
            ref_value = m.group(1) if m.lastindex and m.lastindex >= 1 else ""
            slots = rule.get("slots", {})
            rule_name = rule.get("name", "unknown")
            if "slot_extractor" in rule:
                try:
                    slots = rule["slot_extractor"](m)
                except Exception as e:
                    # R2-17: 异常记录 warning,便于定位 slot_extractor bug
                    _logger.warning(
                        "heuristic_slot_extractor_failed",
                        extra={"rule": rule_name, "err": str(e)},
                    )
                    continue

            return ParsedIntent(
                intent=rule["intent"],
                target_ref={
                    "type": rule["ref_type"],
                    "value": ref_value,
                    "confidence": 0.95,
                },
                slots=slots,
                ambiguity_flag=False,
                clarification_options=[],
                raw_text=text,
                parse_confidence=0.95,
                parse_source="heuristic",
            )
        return None

    def _llm_parse(
        self,
        text: str,
        canvas_summary: str,
        dialog_history: list[dict[str, str]] | None,
    ) -> ParsedIntent:
        """调用 LLM 解析(P-LOW-2-5: 异常分类 + timeout 重试 + fallback)"""
        user_prompt = self._build_user_prompt(text, canvas_summary, dialog_history)
        messages = [
            LLMMessage(role="system", content=INTENT_PARSER_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]

        try:
            data = self.llm.complete_json(messages, temperature=0.2, max_tokens=4096)
            target_ref = data.get("target_ref")
            # 省略恢复: 若 target_ref 是 pronoun 且无 value,从上下文恢复
            if target_ref and target_ref.get("type") == "pronoun" and not target_ref.get("value"):
                restored = self._restore_from_context()
                if restored:
                    target_ref = restored

            return ParsedIntent(
                intent=data.get("intent"),
                target_ref=target_ref,
                slots=data.get("slots", {}),
                ambiguity_flag=data.get("ambiguity_flag", False),
                clarification_options=data.get("clarification_options", []),
                raw_text=text,
                parse_confidence=target_ref.get("confidence", 0.8) if target_ref else 0.5,
                parse_source="llm",
            )
        except Exception as e:
            # P-LOW-2-5: 异常分类处理(独立审核修订 2: 宽匹配 timeout)
            err_name = type(e).__name__.lower()
            err_str = str(e).lower()
            # timeout 类(openai SDK 抛 APITimeoutError/TimeoutError)
            is_timeout = "timeout" in err_name or "timeout" in err_str
            if is_timeout:
                _logger.warning(
                    "llm_timeout_retry",
                    extra={"text_len": len(text), "err_type": type(e).__name__},
                )
                # R2-16: 指数退避(retry_count 从 0 开始,2^0=1s, 2^1=2s, ..., 上限 8s)
                time.sleep(min(2 ** 0, 8))
                try:
                    data = self.llm.complete_json(messages, temperature=0.2, max_tokens=4096)
                    # R2-16: 重试路径也做省略恢复(与首次调用一致)
                    target_ref = data.get("target_ref")
                    if target_ref and target_ref.get("type") == "pronoun" and not target_ref.get("value"):
                        restored = self._restore_from_context()
                        if restored:
                            target_ref = restored
                    return ParsedIntent(
                        intent=data.get("intent"),
                        target_ref=target_ref,
                        slots=data.get("slots", {}),
                        ambiguity_flag=data.get("ambiguity_flag", False),
                        clarification_options=data.get("clarification_options", []),
                        raw_text=text,
                        parse_confidence=(target_ref or {}).get("confidence", 0.8),
                        parse_source="llm",
                    )
                except Exception:
                    _logger.error(
                        "llm_timeout_retry_failed",
                        extra={"text_len": len(text)},
                        exc_info=True,
                    )
                    return self._fallback_ambiguous(text)
            # JSON 解析失败类
            if "json" in err_name or "decode" in err_name or "json" in err_str:
                _logger.warning(
                    "llm_json_decode_failed",
                    extra={"text_len": len(text), "err": str(e)},
                )
                return self._fallback_ambiguous(text)
            # 认证失败类
            if "auth" in err_name or "401" in err_str or "api key" in err_str:
                _logger.error("llm_auth_failed", extra={"err": str(e)})
            else:
                _logger.warning(
                    "llm_parse_failed",
                    extra={"text_len": len(text), "err_type": type(e).__name__, "err": str(e)},
                    exc_info=True,
                )
            return self._fallback_ambiguous(text)

    def _fallback_ambiguous(self, text: str) -> ParsedIntent:
        """P-LOW-2-5: LLM 失败时的兜底 — 返回歧义意图,触发前端澄清"""
        return ParsedIntent(
            intent=None,
            raw_text=text,
            ambiguity_flag=True,
            clarification_options=[],
            parse_confidence=0.0,
            parse_source="fallback",
        )

    def _restore_from_context(self) -> dict[str, Any] | None:
        """省略恢复 — 从上下文栈恢复 target_ref

        当用户说"把它改成异步"但没明确"它"指谁时:
          1. 优先取最近一个 resolved_node_id
          2. 否则取最近一个 target_ref
        """
        for entry in reversed(self._context):
            if entry.resolved_node_id:
                return {
                    "type": "id",
                    "value": entry.resolved_node_id,
                    "confidence": 0.7,
                }
            if entry.target_ref:
                return entry.target_ref
        return None

    def _split_segments(self, text: str) -> list[str]:
        """多意图分段 — 按分隔符拆分

        S3-DST: 一句话含多个意图时分段解析
        """
        # 构建正则: 在分隔符处拆分
        pattern = "|".join(f"({re.escape(sep)})" for sep in INTENT_SEPARATORS)
        parts = re.split(pattern, text)
        # re.split 会在分隔符位置插入分隔符本身,需要过滤
        segments: list[str] = []
        current = ""
        for part in parts:
            if part is None:
                continue
            if part in INTENT_SEPARATORS:
                if current.strip():
                    segments.append(current.strip())
                current = ""
            else:
                current += part
        if current.strip():
            segments.append(current.strip())
        return segments if segments else [text]

    def _is_complete(self, text: str) -> bool:
        """判断文本是否是完整句子"""
        if not text:
            return False
        # 标点结尾
        if text[-1] in "。！？!?.\n":
            return True
        # 含明确动词且长度足够 (>=6 避免片段误判,如 "把 l1")
        verbs = ["改", "删", "加", "连", "查", "创建", "修改", "删除", "连接", "查看", "把", "将"]
        if any(v in text for v in verbs) and len(text) >= 6:
            return True
        # 含节点 ID 引用
        if re.search(r"l[123]-[\w-]+", text, re.IGNORECASE):
            return True
        # 长度足够
        if len(text) >= 10:
            return True
        return False

    def _build_user_prompt(
        self,
        text: str,
        canvas_summary: str,
        dialog_history: list[dict[str, str]] | None,
    ) -> str:
        """构建用户 prompt"""
        parts = [f"用户指令: {text}"]
        if canvas_summary:
            parts.append(f"\n画布状态:\n{canvas_summary}")
        if dialog_history:
            recent = dialog_history[-3:]
            history_str = "\n".join(
                f"  {m['role']}: {m['content'][:60]}" for m in recent
            )
            parts.append(f"\n最近对话:\n{history_str}")
        if self._context:
            last = self._context[-1]
            parts.append(f"\n上一轮意图: {last.intent} → {last.target_ref}")
            if last.resolved_node_id:
                parts.append(f"  (已消解到节点: {last.resolved_node_id})")
        parts.append("\n请解析为 JSON。")
        return "\n".join(parts)

    def _record_context(self, intent: ParsedIntent, segment_index: int) -> None:
        """记录到上下文栈"""
        self._context.append(ContextEntry(
            turn=len(self._context) + 1,
            intent=intent.intent,
            target_ref=intent.target_ref,
            slots=intent.slots,
            raw_text=intent.raw_text,
            timestamp=time.time(),
        ))
        # 修剪上下文栈
        if len(self._context) > self._context_capacity:
            self._context = self._context[-self._context_capacity:]

    def backfill_resolved_node(self, node_id: str | None) -> None:
        """回填 Layer 4 消解结果到最新上下文条目

        供 Layer 4 调用,使后续省略恢复能用到 resolved_node_id
        """
        if self._context and node_id:
            self._context[-1].resolved_node_id = node_id

    def reset_context(self) -> None:
        """重置上下文"""
        self._context.clear()

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """获取解析器统计信息"""
        return {
            "cache": self._cache.stats,
            "context_size": len(self._context),
            "context_capacity": self._context_capacity,
        }
