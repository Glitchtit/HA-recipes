"""Tests for the ingredient matcher's specificity behavior.

These tests stub `_call_ai_json` so the matcher's LLM branch can be exercised
without an API key, and they exercise `_match_ingredient` directly with
in-memory product fixtures.
"""

from __future__ import annotations

import pytest

import backend  # noqa: E402 — conftest stubs SDKs before this import


@pytest.fixture
def products():
    """Mini Storage catalog with one Juusto parent + two specific children."""
    return [
        {"id": 1, "name": "Juusto", "parent_id": None},
        {"id": 2, "name": "Parmesan", "parent_id": 1},
        {"id": 3, "name": "Gouda", "parent_id": 1},
        {"id": 10, "name": "Maito", "parent_id": None},
        {"id": 11, "name": "Suola", "parent_id": None},
    ]


@pytest.fixture
def group_masters():
    """The Group-master subset: parents only."""
    return [
        {"id": 1, "name": "Juusto", "parent_id": None},
        {"id": 10, "name": "Maito", "parent_id": None},
        {"id": 11, "name": "Suola", "parent_id": None},
    ]


class TestMatchIngredientSpecificity:
    def test_generic_matches_parent_loose(self, products, group_masters):
        match = backend._match_ingredient("juusto", products, group_masters=group_masters)
        assert match is not None
        prod, spec = match
        assert prod["id"] == 1
        assert spec == "loose"

    def test_specific_matches_child_strict(self, products, group_masters):
        match = backend._match_ingredient(
            "juusto", products, group_masters=group_masters, specific="parmesan",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 2
        assert spec == "strict"

    def test_specific_with_no_child_falls_back_to_parent_loose(self, products, group_masters):
        # "mozzarella" has no matching product — should fall back to "juusto" parent
        match = backend._match_ingredient(
            "juusto", products, group_masters=group_masters, specific="mozzarella",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 1
        assert spec == "loose"

    def test_specific_match_is_case_insensitive(self, products, group_masters):
        match = backend._match_ingredient(
            "juusto", products, group_masters=group_masters, specific="PARMESAN",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 2
        assert spec == "strict"

    def test_no_match_returns_none(self, products, group_masters):
        assert backend._match_ingredient(
            "kvass", products, group_masters=group_masters,
        ) is None

    def test_legacy_no_group_masters_still_climbs(self, products):
        # Calling without group_masters — exact-match against any product,
        # climbing to parent if matched a child. specific=None → loose.
        match = backend._match_ingredient("Parmesan", products)
        assert match is not None
        prod, spec = match
        # Legacy climb behavior returns the parent
        assert prod["id"] == 1
        assert spec == "loose"


class TestAiMatchIngredientsSpecificity:
    def test_ai_match_writes_specificity(self, products, group_masters, monkeypatch):
        # Stub _call_ai_json to return a strict child match
        def fake_call(_prompt):
            return [
                {"index": 0, "product_id": 2, "specificity": "strict", "confidence": "high"},
            ]
        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        ingredients = [{"name": "juusto", "specific": "parmesan", "_product_id": None}]
        backend._ai_match_ingredients(ingredients, products, group_masters=group_masters)
        assert ingredients[0]["_product_id"] == 2
        assert ingredients[0]["_specificity"] == "strict"

    def test_ai_match_loose_default(self, products, group_masters, monkeypatch):
        def fake_call(_prompt):
            return [
                {"index": 0, "product_id": 1, "specificity": "loose", "confidence": "high"},
            ]
        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        ingredients = [{"name": "juusto", "specific": None, "_product_id": None}]
        backend._ai_match_ingredients(ingredients, products, group_masters=group_masters)
        assert ingredients[0]["_product_id"] == 1
        assert ingredients[0]["_specificity"] == "loose"

    def test_ai_match_rejects_low_confidence(self, products, group_masters, monkeypatch):
        def fake_call(_prompt):
            return [
                {"index": 0, "product_id": 1, "specificity": "loose", "confidence": "low"},
            ]
        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        ingredients = [{"name": "juusto", "specific": None, "_product_id": None}]
        backend._ai_match_ingredients(ingredients, products, group_masters=group_masters)
        assert ingredients[0]["_product_id"] is None

    def test_ai_match_invalid_specificity_coerced_to_loose(self, products, group_masters, monkeypatch):
        def fake_call(_prompt):
            return [
                {"index": 0, "product_id": 1, "specificity": "garbage", "confidence": "high"},
            ]
        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        ingredients = [{"name": "juusto", "specific": None, "_product_id": None}]
        backend._ai_match_ingredients(ingredients, products, group_masters=group_masters)
        assert ingredients[0]["_specificity"] == "loose"

    def test_ai_match_candidate_pool_includes_children(self, products, group_masters, monkeypatch):
        """Verify the matcher offers child products to the LLM, not only parents."""
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return []
        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        ingredients = [{"name": "juusto", "specific": "parmesan", "_product_id": None}]
        backend._ai_match_ingredients(ingredients, products, group_masters=group_masters)
        # Children "Parmesan" and "Gouda" must be present in the prompt's product list
        assert "Parmesan" in captured["prompt"]
        assert "Gouda" in captured["prompt"]
