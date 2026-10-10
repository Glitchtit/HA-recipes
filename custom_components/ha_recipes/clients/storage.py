"""Lightweight client for the HA-storage add-on (recipe suggestions).

Routes (HA-storage/storage/app/routers/recipes.py, Storage 0.27.0+):
  GET /api/recipes/suggest?expiring_within_days -> list[RecipeSuggestion]
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from homeassistant.util.ssl import get_default_context

logger = logging.getLogger(__name__)


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items", []) or []
    return []


class StorageClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def suggest_recipes(self, days: int) -> list[dict[str, Any]]:
        """Recipes cookable from stock, those using stock expiring within `days` first."""
        async with httpx.AsyncClient(verify=get_default_context(), timeout=20) as client:
            r = await client.get(
                f"{self.base_url}/api/recipes/suggest",
                params={"expiring_within_days": int(days)},
            )
            r.raise_for_status()
            return _as_list(r.json())
