"""Prompt guards for the extract step, instruction clean-up, and the thin
delegates to Storage (recipe availability, shopping). Ingredient matching
moved to Storage in 2.8.0 (see test_import_pipeline.py)."""

from __future__ import annotations

import pytest

import backend  # noqa: E402 — conftest stubs SDKs before this import


class TestExtractPromptVariantRules:
    """Prompt-content guard for the LIVE recipe scraping path.

    The scraping pipeline uses _summarize_recipe → _extract_recipe_from_summary,
    NOT _translate_ingredients. The 2.2.1 fix accidentally landed on the dead
    code path. This test asserts the extract prompt — the one actually used
    when recipes are imported — teaches the AI to preserve sugar variants.
    """

    def test_extract_prompt_recognises_swedish_sugar_variants(self, monkeypatch):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        backend._extract_recipe_from_summary("dummy summary", "https://example.com", None)

        prompt = captured["prompt"]
        # Finnish variant target names for the two collapsing cases
        assert "hillosokeri" in prompt, "extract prompt must name hillosokeri (jam sugar)"
        assert "vaniljasokeri" in prompt, "extract prompt must name vaniljasokeri (vanilla sugar)"
        # Swedish source words the AI sees from _summarize_recipe must be enumerated
        assert "syltsocker" in prompt, "extract prompt must name 'syltsocker' as the Swedish source word"
        assert "vaniljsocker" in prompt, "extract prompt must name 'vaniljsocker' as the Swedish source word"

    def test_extract_prompt_translates_instructions_to_english(self, monkeypatch):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

        monkeypatch.setattr(backend, "_call_ai_json", fake_call)
        backend._extract_recipe_from_summary("dummy", "https://example.com", None)

        prompt = captured["prompt"].lower()
        assert "translate to english" in prompt, (
            "extract prompt must instruct the model to translate instructions to English"
        )
        assert "instructions" in prompt


class TestInstructionNumbering:
    def test_strip_instruction_numbering(self):
        raw = [
            "1. Sätt ugnen på 175 grader.",
            "2. Skölj rabarbern.",
            "3) Häll blandningen i pajform.",
            "Servera med vaniljglass.",  # no prefix — pass through
            "1.5 dl vatten i botten.",   # decimal — must NOT be stripped
        ]
        out = backend._strip_instruction_numbering(raw)
        assert out == [
            "Sätt ugnen på 175 grader.",
            "Skölj rabarbern.",
            "Häll blandningen i pajform.",
            "Servera med vaniljglass.",
            "1.5 dl vatten i botten.",
        ]


class TestRecipeDetailDelegatesAvailability:
    """`_get_recipe_detail` no longer computes stock status itself — it merges
    the recipe with Storage's `GET recipes/{id}/availability` payload, which is
    the single source of truth for status (Task 7, recursive stock
    aggregation + pack sizes now live in Storage)."""

    def _setup(self, monkeypatch, *, ingredient, availability_status):
        recipe_payload = {
            "id": 99, "name": "Test Recipe", "description": "Step one\nStep two",
            "source_url": "", "servings": 1, "picture_filename": None,
            "ingredients": [{**ingredient, "unit_id": 8, "unit_abbreviation": "kpl"}],
        }
        avail_payload = {"recipe_id": 99, "ingredients": [{
            "ingredient_id": ingredient["id"], "product_id": ingredient["product_id"],
            "product_name": "X", "parent_id": None, "amount_needed": 2,
            "unit_id": 8, "unit_abbrev": "kpl", "specificity": "loose",
            "status": availability_status, "available": 0}]}

        def fake_api_get(path: str, **_kwargs):
            if path == "recipes/99":
                return recipe_payload
            if path == "recipes/99/availability":
                return avail_payload
            return []

        monkeypatch.setattr(backend, "_api_get", fake_api_get)

    def test_status_comes_from_storage(self, monkeypatch):
        self._setup(monkeypatch,
                    ingredient={"id": 1, "product_id": 5, "amount": 2, "note": "",
                                "specificity": "loose", "product_name": "X"},
                    availability_status="green")
        detail = backend._get_recipe_detail(99)
        assert detail["ingredients"][0]["status"] == "green"
        assert detail["instructions"] == ["Step one", "Step two"]

    def test_substitute_details_come_from_storage(self, monkeypatch):
        self._setup(monkeypatch,
                    ingredient={"id": 1, "product_id": 5, "amount": 2, "note": "",
                                "specificity": "prefer", "product_name": "Ruokakerma kolme juustoa"},
                    availability_status="green")
        avail = backend._api_get("recipes/99/availability")
        avail["ingredients"][0].update(substitute=True, preferred_name="Ruokakerma kolme juustoa",
                                       fallback_name="Ruokakerma")
        ing = backend._get_recipe_detail(99)["ingredients"][0]
        assert (ing["specificity"], ing["substitute"], ing["fallback_name"]) == ("prefer", True, "Ruokakerma")
        assert ing["preferred_name"] == "Ruokakerma kolme juustoa"

    def test_missing_availability_row_defaults_red(self, monkeypatch):
        self._setup(monkeypatch,
                    ingredient={"id": 1, "product_id": 5, "amount": 2, "note": "",
                                "specificity": "loose", "product_name": "X"},
                    availability_status="green")

        # availability payload covers nothing → ingredient defaults to red
        def fake(path, **k):
            if path == "recipes/99/availability":
                return {"recipe_id": 99, "ingredients": []}
            return {
                "id": 99, "name": "r", "description": "", "source_url": "",
                "servings": 1, "picture_filename": None,
                "ingredients": [{"id": 1, "product_id": 5, "amount": 2, "note": "",
                                 "specificity": "loose", "product_name": "X",
                                 "unit_id": 8, "unit_abbreviation": "kpl"}]}
        monkeypatch.setattr(backend, "_api_get", fake)
        detail = backend._get_recipe_detail(99)
        assert detail["ingredients"][0]["status"] == "red"

    def test_availability_fetch_failure_degrades_to_red(self, monkeypatch):
        """If Storage's availability endpoint errors, the detail view must
        still render (status red) instead of raising."""
        recipe_payload = {
            "id": 99, "name": "Test Recipe", "description": "",
            "source_url": "", "servings": 1, "picture_filename": None,
            "ingredients": [{"id": 1, "product_id": 5, "amount": 2, "note": "",
                             "specificity": "loose", "product_name": "X",
                             "unit_id": 8, "unit_abbreviation": "kpl"}],
        }

        def fake_api_get(path: str, **_kwargs):
            if path == "recipes/99":
                return recipe_payload
            if path == "recipes/99/availability":
                raise RuntimeError("storage unreachable")
            return []

        monkeypatch.setattr(backend, "_api_get", fake_api_get)
        detail = backend._get_recipe_detail(99)
        assert detail["ingredients"][0]["status"] == "red"


class TestShoppingDelegatesToStorage:
    """Storage owns the stock and unit math for "add to shopping list"."""

    def test_posts_mode_to_storage(self, monkeypatch):
        calls = []

        def fake_api_post(path, data=None, **_kwargs):
            calls.append((path, data))
            return {"added": 3, "created": 2, "merged": 1}

        monkeypatch.setattr(backend, "_api_post", fake_api_post)
        result = backend._add_to_shopping_list(7, "missing_and_opened")
        assert calls == [("recipes/7/to-shopping", {"mode": "missing_and_opened"})]
        assert result == {"added": 3, "mode": "missing_and_opened"}

    def test_base_unit_rewrite_is_gone(self):
        # Rewriting a product's unit from a recipe reinterpreted its stock.
        assert not hasattr(backend, "_update_product_default_units")
