"""Handler-level tests for ha_recipes services using mocked clients."""
import asyncio
import importlib.util
import pathlib
import sys

import pytest

_HELPERS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ha_recipes"
    / "helpers.py"
)
_spec = importlib.util.spec_from_file_location("ha_recipes_helpers2", _HELPERS)
helpers = importlib.util.module_from_spec(_spec)
sys.modules["ha_recipes_helpers2"] = helpers
_spec.loader.exec_module(helpers)


class FakeRecipes:
    def __init__(self, scrape_result=None, list_result=None, details=None):
        self._scrape = scrape_result or {}
        self._list = list_result or []
        self._details = details or {}
        self.scrape_calls = []

    async def scrape(self, url):
        self.scrape_calls.append(url)
        return self._scrape

    async def list_recipes(self):
        return self._list

    async def recipe_detail(self, rid):
        return self._details[rid]


def test_run_scrape_passes_url_and_returns_payload():
    fake = FakeRecipes(scrape_result={
        "name": "Pannukakku", "servings": 6,
        "ingredients": [{"name": "muna"}], "instructions": ["sekoita"],
        "matches": [{"product_id": 5}],
    })
    out = asyncio.run(helpers.run_scrape(fake, "https://example.com/r"))
    assert fake.scrape_calls == ["https://example.com/r"]
    assert out["name"] == "Pannukakku"
    assert out["servings"] == 6
    assert out["matches"] == [{"product_id": 5}]


class FakeStorage:
    """Answers like HA-storage GET /api/recipes/suggest?expiring_within_days=N."""

    def __init__(self, suggestions=None):
        self._suggestions = suggestions or []
        self.days = None

    async def suggest_recipes(self, days):
        self.days = days
        return self._suggestions


SUGGESTIONS = [
    {"id": 3, "name": "Kermaperunat", "servings": 4, "picture_filename": None,
     "source_url": "https://example.com/3", "missing_count": 0, "yellow_count": 1,
     "uses_expiring": True, "expiring_products": ["Kerma"]},
    {"id": 1, "name": "Pannukakku", "servings": 6, "picture_filename": "p.jpg",
     "source_url": None, "missing_count": 0, "yellow_count": 0,
     "uses_expiring": False, "expiring_products": []},
]


def test_run_suggest_asks_storage_and_keeps_its_order():
    storage = FakeStorage(SUGGESTIONS)
    out = asyncio.run(helpers.run_suggest(storage))
    assert storage.days == 7
    assert out == {"count": 2, "expiring_only": False, "recipes": [
        {"id": 3, "name": "Kermaperunat", "servings": 4, "picture_filename": None,
         "source_url": "https://example.com/3", "uses_expiring": True, "expiring_products": ["Kerma"]},
        {"id": 1, "name": "Pannukakku", "servings": 6, "picture_filename": "p.jpg",
         "source_url": None, "uses_expiring": False, "expiring_products": []},
    ]}


def test_run_suggest_expiring_only_keeps_recipes_using_expiring_stock():
    storage = FakeStorage(SUGGESTIONS)
    out = asyncio.run(helpers.run_suggest(storage, expiring_only=True, days=3))
    assert storage.days == 3
    assert [r["id"] for r in out["recipes"]] == [3]
    assert out["expiring_only"] is True


def test_run_suggest_needs_storage():
    with pytest.raises(RuntimeError, match="HA-storage"):
        asyncio.run(helpers.run_suggest(None))
