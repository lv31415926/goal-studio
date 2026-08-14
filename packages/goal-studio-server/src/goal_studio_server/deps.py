"""依赖注入: 共享 SessionManager / AdapterRegistry / LLM"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from goal_studio.core.adapter_registry import AdapterRegistry, get_default_registry
from goal_studio.core.session_manager import SessionManager
from goal_studio.llm.base import BaseLLMProvider
from goal_studio.llm.registry import get_llm

if TYPE_CHECKING:
    from goal_studio.core.artifact_store import LocalFileArtifactStore


@lru_cache(maxsize=1)
def get_session_manager() -> SessionManager:
    """获取全局 SessionManager

    存储路径优先级:
      1. 环境变量 GOAL_STUDIO_SESSION_ROOT (部署/沙盒场景)
      2. 默认 ~/.goal-studio/sessions
    """
    import os
    root_env = os.getenv("GOAL_STUDIO_SESSION_ROOT")
    root = Path(root_env) if root_env else Path.home() / ".goal-studio" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return SessionManager(root=root)


@lru_cache(maxsize=1)
def get_artifact_store() -> LocalFileArtifactStore:
    """获取全局 ArtifactStore (PR-S3)

    存储路径优先级:
      1. 环境变量 GOAL_STUDIO_ARTIFACT_ROOT (部署场景)
      2. 默认 <cwd>/artifacts
    """
    from goal_studio.core.artifact_store import LocalFileArtifactStore

    root_env = os.getenv("GOAL_STUDIO_ARTIFACT_ROOT")
    root = Path(root_env) if root_env else Path.cwd() / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return LocalFileArtifactStore(root=root)


@lru_cache(maxsize=1)
def get_adapter_registry() -> AdapterRegistry:
    """获取全局适配器注册中心"""
    return get_default_registry()


@lru_cache(maxsize=1)
def get_llm_provider() -> BaseLLMProvider:
    """获取全局 LLM Provider"""
    return get_llm()
