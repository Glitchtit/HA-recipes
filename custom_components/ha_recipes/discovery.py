"""Discover sibling add-on URLs via the Supervisor API."""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

import httpx
from homeassistant.util.ssl import get_default_context

logger = logging.getLogger(__name__)

SUPERVISOR_BASE = "http://supervisor"


async def _list_addons(token: str) -> list[dict]:
    async with httpx.AsyncClient(verify=get_default_context(), timeout=5) as client:
        r = await client.get(
            f"{SUPERVISOR_BASE}/addons",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return (r.json().get("data") or {}).get("addons", []) or []


async def _addon_info(token: str, slug: str) -> dict:
    async with httpx.AsyncClient(verify=get_default_context(), timeout=5) as client:
        r = await client.get(
            f"{SUPERVISOR_BASE}/addons/{slug}/info",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json().get("data") or {}


async def discover_addon_url(
    *,
    slug_patterns: Iterable[str],
    port: int,
    health_path: str = "/api/health",
) -> str | None:
    """Find a running add-on whose slug matches any of `slug_patterns` (case-insensitive).

    Returns `http://<hostname>:<port>` if found, else None.
    """
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        logger.debug("No SUPERVISOR_TOKEN; skipping discovery")
        return None
    try:
        addons = await _list_addons(token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("List add-ons failed: %s", exc)
        return None

    patterns = [re.compile(p, re.IGNORECASE) for p in slug_patterns]
    for addon in addons:
        slug = addon.get("slug") or ""
        name = addon.get("name") or ""
        if any(p.search(slug) or p.search(name) for p in patterns):
            try:
                info = await _addon_info(token, slug)
            except Exception:  # noqa: BLE001
                continue
            host = info.get("hostname") or info.get("ip_address")
            if not host:
                continue
            url = f"http://{host}:{port}"
            # Verify with a quick health probe, but don't fail discovery if 404
            try:
                async with httpx.AsyncClient(verify=get_default_context(), timeout=3) as client:
                    r = await client.get(f"{url}{health_path}")
                    if r.status_code < 500:
                        return url
            except Exception:  # noqa: BLE001
                pass
            return url
    return None
