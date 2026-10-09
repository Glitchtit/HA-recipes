"""Config flow for the HA-recipes integration."""
from __future__ import annotations

import logging
import os

import httpx
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.util.ssl import get_default_context

from .const import (
    ADDON_SLUG_RECIPES_PATTERN,
    CONF_ADDON_URL,
    DEFAULT_ADDON_URL,
    DOMAIN,
    RECIPES_PORT,
)

_LOGGER = logging.getLogger(__name__)


async def _discover_addon_url() -> str | None:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=5) as client:
            r = await client.get(
                f"http://supervisor/addons/{ADDON_SLUG_RECIPES_PATTERN}/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 200:
                data = r.json().get("data") or {}
                host = data.get("hostname") or data.get("ip_address")
                if host:
                    return f"http://{host}:{RECIPES_PORT}"
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Supervisor add-on discovery failed: %s", exc)
    return None


async def _test_connection(url: str) -> bool:
    # The HA-recipes backend has no /api/health; /api/config always returns 200
    # when the backend is up, so use it as the reachability probe.
    try:
        async with httpx.AsyncClient(verify=get_default_context(), timeout=5) as client:
            r = await client.get(f"{url.rstrip('/')}/api/config")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


class RecipesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        discovered = await _discover_addon_url()

        if user_input is not None:
            addon_url = user_input[CONF_ADDON_URL].rstrip("/")
            if not await _test_connection(addon_url):
                errors[CONF_ADDON_URL] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured(
                    updates={CONF_ADDON_URL: addon_url}
                )
                return self.async_create_entry(
                    title="Recipes",
                    data={CONF_ADDON_URL: addon_url},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDON_URL,
                        default=discovered or DEFAULT_ADDON_URL,
                    ): str,
                }
            ),
            errors=errors,
        )
