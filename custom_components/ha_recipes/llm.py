"""Hands Recipes's actions to voice assistants as Assist LLM tools.

Home Assistant (2026.11) asks every integration's llm.py for tools; without
this file an assistant sees none of these actions.
"""

from __future__ import annotations

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.llm import LLM_API_ASSIST, ActionTool, LLMContext

from .const import DOMAIN

# scrape_url is left out: nobody dictates a URL.
ACTIONS = ("search", "suggest_from_stock")


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return Recipes's tools for the Assist API."""
    if api_id != LLM_API_ASSIST:
        return None
    tools = [
        ActionTool(hass, DOMAIN, action)
        for action in ACTIONS
        if hass.services.has_service(DOMAIN, action)
    ]
    return LLMTools(tools=tools) if tools else None
