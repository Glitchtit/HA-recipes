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


class TestTranslatePromptVariantRules:
    """Prompt-content regression guard for the rabarberpaj bug.

    The translation prompt must teach the AI to preserve non-interchangeable
    sugar/fat/flour/dairy variants instead of collapsing them onto plain
    generics. We assert on the prompt text (captured from the mocked
    _call_ai_json) rather than the model's output, so the test is
    deterministic and fails loudly if a future edit drops the variant rules.
    """

    def test_translate_prompt_includes_variant_reasoning_rules(self, monkeypatch):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return []

        monkeypatch.setattr(backend, "_call_ai_json", fake_call)

        backend._translate_ingredients(["1 dl syltsocker"])

        prompt = captured["prompt"]
        # Reasoning principle present in some form
        assert "swap" in prompt.lower() or "interchangeable" in prompt.lower(), (
            "Prompt must teach the swap-test reasoning principle"
        )
        # Sugar variant exemplars
        assert "syltsocker" in prompt and "hillosokeri" in prompt
        assert "vaniljsocker" in prompt and "vaniljasokeri" in prompt
        assert "tomusokeri" in prompt  # powdered
        assert "fariinisokeri" in prompt  # brown
        # Fat variants
        assert "margariini" in prompt
        # Flour variants (rye was already there; check a newly added one)
        assert "mantelijauho" in prompt or "speltijauho" in prompt
        # Dairy
        assert "vispikerma" in prompt


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


class TestCreateChildStubsForUnmatchedSpecifics:
    """Auto-create child products for ingredients that matched a parent
    loosely but named a non-interchangeable specific variant.

    Closes the architectural gap behind the rabarberpaj bug: even with the
    extract prompt emitting specific="hillosokeri", the matcher falls back
    to the parent Sokeri loosely and no child product is ever created.
    This pass creates the child and re-binds the ingredient as strict.
    """

    @pytest.fixture
    def products_with_sokeri_parent(self):
        return [
            {"id": 10, "name": "Sokeri", "parent_id": None, "unit_id": 4, "location_id": 1, "product_group_id": 7},
            {"id": 99, "name": "Voi", "parent_id": None, "unit_id": 1, "location_id": 1},
        ]

    def test_creates_child_for_unmatched_specific(self, products_with_sokeri_parent, monkeypatch):
        posts: list[tuple[str, dict]] = []

        def fake_api_post(path, data=None, **_kwargs):
            posts.append((path, data))
            return {"id": 200}

        monkeypatch.setattr(backend, "_api_post", fake_api_post)

        ingredients = [
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 10, "_specificity": "loose"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(
            ingredients, products_with_sokeri_parent,
        )

        assert created == {200}
        assert len(posts) == 1
        path, body = posts[0]
        assert path == "products"
        assert body["name"] == "hillosokeri"
        assert body["parent_id"] == 10
        assert body["active"] is False
        # Parent's product_group_id propagates so the child lands under the same group
        assert body.get("product_group_id") == 7
        # Ingredient re-bound to the new child as strict
        assert ingredients[0]["_product_id"] == 200
        assert ingredients[0]["_specificity"] == "strict"

    def test_reuses_existing_child(self, products_with_sokeri_parent, monkeypatch):
        products = products_with_sokeri_parent + [
            {"id": 201, "name": "Hillosokeri", "parent_id": 10},
        ]
        posts: list = []
        monkeypatch.setattr(backend, "_api_post",
                            lambda *a, **kw: posts.append((a, kw)) or {"id": -1})

        ingredients = [
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 10, "_specificity": "loose"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(ingredients, products)

        assert created == set()
        assert posts == []
        # Re-bound to the existing child as strict
        assert ingredients[0]["_product_id"] == 201
        assert ingredients[0]["_specificity"] == "strict"

    def test_dedup_within_same_recipe(self, products_with_sokeri_parent, monkeypatch):
        next_id = [200]
        posts: list = []

        def fake_api_post(path, data=None, **_kwargs):
            posts.append((path, data))
            cur = next_id[0]
            next_id[0] += 1
            return {"id": cur}

        monkeypatch.setattr(backend, "_api_post", fake_api_post)

        ingredients = [
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 10, "_specificity": "loose"},
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 10, "_specificity": "loose"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(
            ingredients, products_with_sokeri_parent,
        )

        assert len(posts) == 1
        assert created == {200}
        assert ingredients[0]["_product_id"] == 200
        assert ingredients[1]["_product_id"] == 200

    def test_skips_strict_match(self, products_with_sokeri_parent, monkeypatch):
        posts: list = []
        monkeypatch.setattr(backend, "_api_post",
                            lambda *a, **kw: posts.append((a, kw)) or {"id": -1})

        ingredients = [
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 42, "_specificity": "strict"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(
            ingredients, products_with_sokeri_parent,
        )

        assert created == set()
        assert posts == []

    def test_skips_when_specific_is_null(self, products_with_sokeri_parent, monkeypatch):
        posts: list = []
        monkeypatch.setattr(backend, "_api_post",
                            lambda *a, **kw: posts.append((a, kw)) or {"id": -1})

        ingredients = [
            {"name": "sokeri", "specific": None, "_product_id": 10, "_specificity": "loose"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(
            ingredients, products_with_sokeri_parent,
        )

        assert created == set()
        assert posts == []

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

    def test_climbs_when_matched_product_is_a_child(self, monkeypatch):
        """When the matched product is itself a child (e.g. Sokeri is a child
        of Makeutusaineet), the new variant stub should land as a sibling of
        the matched product (under the grandparent), not as a grandchild."""
        products = [
            {"id": 5, "name": "Makeutusaineet", "parent_id": None, "unit_id": 4, "location_id": 1, "product_group_id": 7},
            {"id": 10, "name": "Sokeri", "parent_id": 5, "unit_id": 4, "location_id": 1, "product_group_id": 7},
        ]
        posts: list[tuple[str, dict]] = []

        def fake_api_post(path, data=None, **_kwargs):
            posts.append((path, data))
            return {"id": 200}

        monkeypatch.setattr(backend, "_api_post", fake_api_post)

        ingredients = [
            {"name": "sokeri", "specific": "hillosokeri", "_product_id": 10, "_specificity": "loose"},
        ]

        created = backend._create_child_stubs_for_unmatched_specifics(ingredients, products)

        assert created == {200}
        assert len(posts) == 1
        _path, body = posts[0]
        # Stub created under Makeutusaineet (id=5), not under Sokeri (id=10)
        assert body["parent_id"] == 5
        assert body["name"] == "hillosokeri"
        assert ingredients[0]["_product_id"] == 200
        assert ingredients[0]["_specificity"] == "strict"
