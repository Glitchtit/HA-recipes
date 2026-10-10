"""Pure, HA-free helpers for the ha_recipes integration.

Kept import-light (stdlib only) so they unit-test without Home Assistant.
"""
from __future__ import annotations

from typing import Any


def filter_recipes_by_query(
    recipes: list[dict[str, Any]], query: str
) -> list[dict[str, Any]]:
    """Case-insensitive substring filter on recipe ``name``.

    A blank/whitespace query returns the list unchanged (acts as "list all").
    The HA-recipes backend has no search route, so search is done client-side
    over the recipe list payload, whose only text field is ``name``.
    """
    q = (query or "").strip().lower()
    if not q:
        return recipes
    return [r for r in recipes if q in (r.get("name") or "").lower()]


async def run_scrape(recipes_client: Any, url: str) -> dict[str, Any]:
    """Proxy a scrape request and return the parsed recipe + Storage matches.

    `recipes_client` must expose an async `scrape(url) -> dict`.
    """
    return await recipes_client.scrape(url)


DEFAULT_EXPIRING_DAYS = 7


async def run_suggest(
    storage_client: Any | None,
    *,
    expiring_only: bool = False,
    days: int | None = None,
) -> dict[str, Any]:
    """Suggest recipes cookable from current Storage stock.

    HA-storage's GET /api/recipes/suggest does the work: a recipe is cookable
    when it has ingredients and none is missing, counting stock anywhere under
    each ingredient, and recipes using stock that expires within ``days``
    (default 7) come first with ``uses_expiring`` set. With ``expiring_only``
    only those are kept.

    `storage_client` needs async `suggest_recipes(days) -> list[dict]`.
    """
    if storage_client is None:
        raise RuntimeError("HA-storage add-on was not auto-discovered; cannot suggest recipes")
    window = days if days is not None else DEFAULT_EXPIRING_DAYS
    suggestions = await storage_client.suggest_recipes(window)
    if expiring_only:
        suggestions = [s for s in suggestions if s.get("uses_expiring")]
    slimmed = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "servings": s.get("servings"),
            "picture_filename": s.get("picture_filename"),
            "source_url": s.get("source_url"),
            "uses_expiring": bool(s.get("uses_expiring")),
            "expiring_products": s.get("expiring_products") or [],
        }
        for s in suggestions
    ]
    return {
        "count": len(slimmed),
        "expiring_only": bool(expiring_only),
        "recipes": slimmed,
    }
