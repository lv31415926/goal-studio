"""KnowledgeStore: 知识存储抽象接口

存储创作过程中积累的知识(wiki/experience/pinned),供后续创作复用。
默认提供 LocalFileKnowledgeStore 和 SQLiteKnowledgeStore 两实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class KnowledgeEntry:
    """知识条目"""

    id: str
    """唯一 ID"""

    category: str
    """类别: wiki / experience / pinned / pattern"""

    key: str
    """键(如 'skill-creation.tip' / 'adapter.pattern')"""

    value: str
    """值(文本或 JSON 字符串)"""

    source: str = "manual"
    """来源: manual / auto / peer-review"""

    confidence: float = 0.5
    """可信度 0-1"""

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeStore(Protocol):
    """知识存储协议"""

    def get(self, key: str) -> KnowledgeEntry | None: ...

    def query(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[KnowledgeEntry]: ...

    def put(self, entry: KnowledgeEntry) -> None: ...

    def update(self, key: str, value: str, confidence: float | None = None) -> None: ...

    def delete(self, key: str) -> bool: ...

    def list_categories(self) -> list[str]: ...


class InMemoryKnowledgeStore:
    """内存实现(测试用)"""

    def __init__(self) -> None:
        self._data: dict[str, KnowledgeEntry] = {}

    def get(self, key: str) -> KnowledgeEntry | None:
        return self._data.get(key)

    def query(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[KnowledgeEntry]:
        result = list(self._data.values())
        if category:
            result = [e for e in result if e.category == category]
        if tags:
            tag_set = set(tags)
            result = [e for e in result if tag_set & set(e.tags)]
        if keyword:
            kw = keyword.lower()
            result = [
                e for e in result
                if kw in e.key.lower() or kw in e.value.lower()
            ]
        return result

    def put(self, entry: KnowledgeEntry) -> None:
        self._data[entry.key] = entry

    def update(self, key: str, value: str, confidence: float | None = None) -> None:
        if key in self._data:
            self._data[key].value = value
            self._data[key].updated_at = datetime.now()
            if confidence is not None:
                self._data[key].confidence = confidence

    def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    def list_categories(self) -> list[str]:
        return sorted({e.category for e in self._data.values()})


_ = Any
