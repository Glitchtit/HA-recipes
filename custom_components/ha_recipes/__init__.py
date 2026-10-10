"""HA-recipes custom integration.

Services-only integration (no entities). Discovers the HA-recipes and HA-storage
add-ons via the Supervisor API and exposes three response-returning services for
conversational agents:
  - ha_recipes.search             search saved recipes by name
  - ha_recipes.scrape_url         import a recipe from a web URL
  - ha_recipes.suggest_from_stock suggest recipes cookable from current stock
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .clients.recipes import RecipesClient
from .clients.storage import StorageClient
from .const import (
    ADDON_SLUG_RECIPES_PATTERN,
    ADDON_SLUG_STORAGE_PATTERN,
    CONF_ADDON_URL,
    DOMAIN,
    RECIPES_PORT,
    SERVICE_SCRAPE_URL,
    SERVICE_SEARCH,
    SERVICE_SUGGEST_FROM_STOCK,
    STORAGE_PORT,
)
from .discovery import discover_addon_url
from .helpers import filter_recipes_by_query, run_scrape, run_suggest

_LOGGER = logging.getLogger(__name__)

SEARCH_SCHEMA = vol.Schema({vol.Required("query"): str})
SCRAPE_SCHEMA = vol.Schema({vol.Required("url"): str})
SUGGEST_SCHEMA = vol.Schema(
    {
        vol.Optional("expiring_only", default=False): bool,
        vol.Optional("days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    addon_url = entry.data[CONF_ADDON_URL]

    # The configured URL is the HA-recipes backend itself. Discover it via the
    # Supervisor API too (so we follow the add-on if its hostname changes); fall
    # back to the configured URL.
    recipes_url = (
        await discover_addon_url(
            slug_patterns=[ADDON_SLUG_RECIPES_PATTERN],
            port=RECIPES_PORT,
            health_path="/api/config",
        )
        or addon_url
    )
    storage_url = await discover_addon_url(
        slug_patterns=[ADDON_SLUG_STORAGE_PATTERN], port=STORAGE_PORT
    )

    recipes = RecipesClient(recipes_url) if recipes_url else None
    storage = StorageClient(storage_url) if storage_url else None

    hass.data[DOMAIN][entry.entry_id] = {"recipes": recipes, "storage": storage}

    async def _search_service(call: ServiceCall) -> dict:
        if recipes is None:
            raise HomeAssistantError(
                "HA-recipes add-on was not auto-discovered; cannot search recipes."
            )
        try:
            all_recipes = await recipes.list_recipes()
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to list recipes: {exc}") from exc
        matched = filter_recipes_by_query(all_recipes, call.data["query"])
        return {"count": len(matched), "recipes": matched}

    async def _scrape_service(call: ServiceCall) -> dict:
        if recipes is None:
            raise HomeAssistantError(
                "HA-recipes add-on was not auto-discovered; cannot scrape."
            )
        try:
            return await run_scrape(recipes, call.data["url"])
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Scrape failed: {exc}") from exc

    async def _suggest_service(call: ServiceCall) -> dict:
        if storage is None:
            raise HomeAssistantError(
                "HA-storage add-on was not auto-discovered; cannot suggest recipes."
            )
        try:
            return await run_suggest(
                storage,
                expiring_only=call.data.get("expiring_only", False),
                days=call.data.get("days"),
            )
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to suggest recipes: {exc}") from exc

    if not hass.services.has_service(DOMAIN, SERVICE_SEARCH):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEARCH,
            _search_service,
            schema=SEARCH_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SCRAPE_URL):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCRAPE_URL,
            _scrape_service,
            schema=SCRAPE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SUGGEST_FROM_STOCK):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SUGGEST_FROM_STOCK,
            _suggest_service,
            schema=SUGGEST_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SEARCH)
        hass.services.async_remove(DOMAIN, SERVICE_SCRAPE_URL)
        hass.services.async_remove(DOMAIN, SERVICE_SUGGEST_FROM_STOCK)
    return True
