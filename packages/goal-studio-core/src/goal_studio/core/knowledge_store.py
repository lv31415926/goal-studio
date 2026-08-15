"""KnowledgeStore: 知识存储抽象接口

存储创作过程中积累的知识(wiki/experience/pinned),供后续创作复用。
默认提供 LocalFileKnowledgeStore 和 SQLiteKnowledgeStore 两实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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


class LocalFileKnowledgeStore:
    """本地 JSON 文件持久化实现(单进程/低并发场景)

    G1 接线: 存储创作过程的反思经验,供后续 draft 检索注入。
    格式: 单个 JSON 文件 {"entries": {key: entry_dict}}。
    写入原子替换(tmp + os.replace),读加载带线程锁。
    """

    def __init__(self, path: str | Path) -> None:
        import json
        import threading

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._json = json
        self._data: dict[str, KnowledgeEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._json.loads(self._path.read_text(encoding="utf-8"))
            for d in raw.get("entries", {}).values():
                e = KnowledgeEntry(
                    id=d["id"],
                    category=d["category"],
                    key=d["key"],
                    value=d["value"],
                    source=d.get("source", "manual"),
                    confidence=d.get("confidence", 0.5),
                    tags=d.get("tags", []),
                    metadata=d.get("metadata", {}),
                )
                for k in ("created_at", "updated_at"):
                    if d.get(k):
                        setattr(e, k, datetime.fromisoformat(d[k]))
                self._data[e.key] = e
        except Exception:  # noqa: BLE001 — 损坏文件降级为空库,不阻断启动
            self._data = {}

    def _flush(self) -> None:
        import os
        import tempfile

        payload = {"entries": {k: {
            "id": e.id, "category": e.category, "key": e.key, "value": e.value,
            "source": e.source, "confidence": e.confidence, "tags": e.tags,
            "metadata": e.metadata,
            "created_at": e.created_at.isoformat(),
            "updated_at": e.updated_at.isoformat(),
        } for k, e in self._data.items()}}
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                self._json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, str(self._path))
        except Exception:
            os.unlink(tmp)
            raise

    def get(self, key: str) -> KnowledgeEntry | None:
        with self._lock:
            return self._data.get(key)

    def query(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[KnowledgeEntry]:
        with self._lock:
            result = list(self._data.values())
        if category:
            result = [e for e in result if e.category == category]
        if tags:
            tag_set = set(tags)
            result = [e for e in result if tag_set & set(e.tags)]
        if keyword:
            kw = keyword.lower()
            result = [e for e in result if kw in e.key.lower() or kw in e.value.lower()]
        return result

    def put(self, entry: KnowledgeEntry) -> None:
        with self._lock:
            self._data[entry.key] = entry
            self._flush()

    def update(self, key: str, value: str, confidence: float | None = None) -> None:
        with self._lock:
            if key in self._data:
                self._data[key].value = value
                self._data[key].updated_at = datetime.now()
                if confidence is not None:
                    self._data[key].confidence = confidence
                self._flush()

    def delete(self, key: str) -> bool:
        with self._lock:
            if self._data.pop(key, None) is None:
                return False
            self._flush()
            return True

    def list_categories(self) -> list[str]:
        with self._lock:
            return sorted({e.category for e in self._data.values()})
