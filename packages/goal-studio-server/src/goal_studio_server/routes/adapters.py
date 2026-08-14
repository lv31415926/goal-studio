"""适配器信息 REST API"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from goal_studio.core.adapter_registry import AdapterRegistry

from goal_studio_server.deps import get_adapter_registry
from goal_studio_server.models import AdapterInfo

router = APIRouter()


@router.get("", response_model=list[AdapterInfo])
async def list_adapters(
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> list[AdapterInfo]:
    """列出所有已注册适配器"""
    return [AdapterInfo(**a) for a in registry.list_adapters()]
