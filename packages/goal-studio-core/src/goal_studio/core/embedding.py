"""Embedding provider: 知识检索的向量相似度升级

检索升级路径: 关键词重合度 → embedding 余弦相似度。
默认关闭(GOAL_STUDIO_EMBEDDING_PROVIDER=none),保持零行为变化;
可选 hash(确定性词袋哈希,零依赖,离线测试)/openai(可选依赖)。

降级契约: get_embedding_provider() 返回 None 或 embed 抛异常时,
调用方回退关键词检索路径。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    """文本向量协议"""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokenize(text: str) -> list[str]:
    """中英混合分词: 英文词 + 中文 bigram"""
    tokens = [w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{1,}", text)]
    han = re.findall(r"[\u4e00-\u9fff]", text)
    tokens += [han[i] + han[i + 1] for i in range(len(han) - 1)]
    return [t for t in tokens if t]


class HashEmbeddingProvider:
    """确定性词袋哈希向量(零依赖,离线可用)

    每个 token 哈希到 [0, dim) 桶并累加,L2 归一化。
    相同文本 → 相同向量;共享 token 越多余弦越高。
    语义能力有限(无上下文),适合测试与无外网环境的检索升级验证。
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return "hash"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for tok in _tokenize(text):
                bucket = int.from_bytes(
                    hashlib.md5(tok.encode("utf-8")).digest()[:4], "little"
                ) % self._dim
                vec[bucket] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


class OpenAIEmbeddingProvider:
    """OpenAI embedding(可选依赖,惰性加载)"""

    def __init__(self, model: str = "text-embedding-3-small", dim: int = 1536) -> None:
        self._model = model
        self._dim = dim

    @property
    def name(self) -> str:
        return "openai"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import os as _os

        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai 未安装,无法使用 embedding") from e
        client = OpenAI(api_key=_os.getenv("OPENAI_API_KEY", ""))
        resp = client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


def get_embedding_provider() -> EmbeddingProvider | None:
    """工厂: 按 GOAL_STUDIO_EMBEDDING_PROVIDER 配置

    - "none"(默认): 返回 None — 检索走关键词路径(行为与升级前一致)
    - "hash": 词袋哈希(离线)
    - "openai": OpenAI API(需 OPENAI_API_KEY)
    """
    kind = os.getenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "none").lower()
    if kind in ("", "none", "off", "0"):
        return None
    if kind == "hash":
        return HashEmbeddingProvider()
    if kind == "openai":
        model = os.getenv("GOAL_STUDIO_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbeddingProvider(model=model)
    raise ValueError(f"Unknown embedding provider: {kind}")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度(维度不匹配返回 -1,调用方按最低分处理)"""
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
