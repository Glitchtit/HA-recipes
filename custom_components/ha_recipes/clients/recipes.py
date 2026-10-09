"""Lightweight client for the HA-recipes add-on backend.

Routes (confirmed against HA-recipes/recipes/backend.py, a raw http.server):
  GET  /api/recipes            -> {"success": bool, "recipes": [...]}
  GET  /api/recipe/<id>        -> {"success": bool, "recipe": {...}}
  POST /api/recipe/scrape      -> {"success": bool, ...parsed recipe...}
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from homeassistant.util.ssl import get_default_context

logger = logging.getLogger(__name__)


def _unwrap(data: dict[str, Any], key: str | None = None) -> Any:
    """Validate the backend's {"success": ...} envelope and return the payload."""
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response: {data!r}")
    if data.get("success") is False:
        raise RuntimeError(data.get("error") or "Recipes backend reported failure")
    if key is not None:
        return data.get(key)
    return data


class RecipesClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def list_recipes(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=15) as client:
            r = await client.get(f"{self.base_url}/api/recipes")
            r.raise_for_status()
            payload = _unwrap(r.json(), "recipes")
        return payload or []

    async def recipe_detail(self, recipe_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=20) as client:
            r = await client.get(f"{self.base_url}/api/recipe/{int(recipe_id)}")
            if r.status_code == 404:
                raise FileNotFoundError(f"Recipe {recipe_id} not found")
            r.raise_for_status()
            return _unwrap(r.json(), "recipe") or {}

    async def scrape(self, url: str) -> dict[str, Any]:
        """POST a web URL to the scrape pipeline. Returns the parsed recipe
        plus Storage match info. Backend strips the envelope's "success" key
        and merges the rest, so we return the whole dict minus "success"."""
        async with httpx.AsyncClient(verify=get_default_context(), timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/recipe/scrape", json={"url": url}
            )
            r.raise_for_status()
            data = _unwrap(r.json())
        out = dict(data)
        out.pop("success", None)
        return out
