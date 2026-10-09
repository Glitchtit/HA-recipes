"""Lightweight client for the HA-storage add-on (stock lookups).

Routes (confirmed against HA-storage/storage/app/routers/stock.py):
  GET /api/stock/entries?expiring_within_days -> list[StockEntryWithProduct]
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

    async def fetch_expiring_entries(self, days: int) -> list[dict[str, Any]]:
        """Stock lots whose best_before_date falls within `days` days."""
        async with httpx.AsyncClient(verify=get_default_context(), timeout=10) as client:
            r = await client.get(
                f"{self.base_url}/api/stock/entries",
                params={"expiring_within_days": int(days)},
            )
            r.raise_for_status()
            return _as_list(r.json())
