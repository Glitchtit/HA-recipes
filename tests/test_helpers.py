"""Pure-helper unit tests for the ha_recipes integration (no HA harness)."""
import importlib.util
import pathlib
import sys

# Load helpers.py directly so we don't need the homeassistant package installed.
_HELPERS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ha_recipes"
    / "helpers.py"
)
_spec = importlib.util.spec_from_file_location("ha_recipes_helpers", _HELPERS)
helpers = importlib.util.module_from_spec(_spec)
sys.modules["ha_recipes_helpers"] = helpers
_spec.loader.exec_module(helpers)


RECIPES = [
    {"id": 1, "name": "Lihapullat ja perunamuusi", "servings": 4},
    {"id": 2, "name": "Kasvissosekeitto", "servings": 2},
    {"id": 3, "name": "Pannukakku", "servings": 6},
]


def test_filter_recipes_by_query_substring_case_insensitive():
    out = helpers.filter_recipes_by_query(RECIPES, "PANNU")
    assert [r["id"] for r in out] == [3]


def test_filter_recipes_by_query_blank_returns_all():
    assert helpers.filter_recipes_by_query(RECIPES, "") == RECIPES
    assert helpers.filter_recipes_by_query(RECIPES, "   ") == RECIPES


def test_filter_recipes_by_query_no_match_returns_empty():
    assert helpers.filter_recipes_by_query(RECIPES, "sushi") == []
