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

    def test_specific_matching_a_parent_demotes_to_loose(self, group_masters):
        """When the AI's `specific` value happens to equal a PARENT product's
        name (e.g. specific="punasipuli" and "Punasipuli" is a top-level
        product with sub-variant children), stage 1 should return the parent
        as LOOSE — not strict — so the downstream status calculation
        aggregates child stock. Otherwise the user's only stocked variant
        ("Punasipuli 500g Suomi 2lk") would be ignored."""
        products = [
            {"id": 1350, "name": "Punasipuli", "parent_id": None, "active": True},
            {"id": 1351, "name": "Punasipuli 500g Suomi 2lk", "parent_id": 1350, "active": True},
        ]
        match = backend._match_ingredient(
            "sipuli", products, group_masters=group_masters, specific="punasipuli",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 1350
        assert spec == "loose"

    def test_specific_matching_a_real_child_stays_strict(self, group_masters):
        """The strict path stays strict when `specific` resolves to a true
        child product (parent_id is set), so variant-aware matching keeps
        working for things like parmesan/gouda under a Juusto parent."""
        products = [
            {"id": 1, "name": "Juusto", "parent_id": None, "active": True},
            {"id": 2, "name": "Parmesan", "parent_id": 1, "active": True},
        ]
        match = backend._match_ingredient(
            "juusto", products, group_masters=group_masters, specific="parmesan",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 2
        assert spec == "strict"

    def test_prefers_active_when_duplicate_name(self, group_masters):
        """When two products share a name (typically: a user-curated active
        product and an auto-stub inactive product), prefer the active one
        so stock state surfaces correctly. Both are parents here, so the
        match is loose (see test_specific_matching_a_parent_demotes_to_loose)."""
        products = [
            # Inactive auto-stub created by an earlier scrape, listed first
            {"id": 1350, "name": "Punasipuli", "parent_id": None, "active": False},
            # User's actual curated Punasipuli, active
            {"id": 2000, "name": "Punasipuli", "parent_id": None, "active": True},
        ]
        match = backend._match_ingredient(
            "sipuli", products, group_masters=group_masters, specific="punasipuli",
        )
        assert match is not None
        prod, spec = match
        assert prod["id"] == 2000
        assert spec == "loose"

    def test_falls_back_to_inactive_when_no_active(self, group_masters):
        """If the only match is inactive, still return it (the user might
        activate it later). Keeps backwards compatibility for catalogs that
        only have inactive auto-stubs."""
        products = [
            {"id": 1350, "name": "Punasipuli", "parent_id": None, "active": False},
        ]
        match = backend._match_ingredient(
            "sipuli", products, group_masters=group_masters, specific="punasipuli",
        )
        assert match is not None
        prod, _spec = match
        assert prod["id"] == 1350

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


class TestRecipeDetailStrictOnParentAggregates:
    """`_get_recipe_detail` must aggregate children stock when a strict match
    landed on a top-level parent product (parent_id=None). This recovers the
    Punasipuli rabarberpaj case without re-scraping: the AI emitted
    specific="punasipuli", the matcher bound the parent "Punasipuli" strictly
    (pre-2.2.11 behaviour, still in stored recipes), and the user's child
    products like "Punasipuli 500g Suomi 2lk" have the actual stock.

    Strict-on-CHILD (parent_id != None) must still ignore siblings.
    """

    def _setup(self, monkeypatch, *, ingredient, recipe_unit_id, products, stock):
        recipe_payload = {
            "id": 99,
            "name": "Test Recipe",
            "description": "",
            "source_url": "",
            "servings": 1,
            "picture_filename": None,
            "ingredients": [
                {
                    **ingredient,
                    "unit_id": recipe_unit_id,
                    "unit_abbreviation": "kpl",
                }
            ],
        }

        def fake_api_get(path: str, **_kwargs):
            if path == "recipes/99":
                return recipe_payload
            if path == "stock":
                return stock
            if path.startswith("products"):
                return products
            if path == "conversions":
                return []
            return []

        monkeypatch.setattr(backend, "_api_get", fake_api_get)

    def test_strict_on_parent_aggregates_children(self, monkeypatch):
        """specificity=strict + matched a parent (parent_id=None) → walks
        children's stock. Recovers pre-2.2.11 stored bindings."""
        products = [
            {"id": 100, "name": "Punasipuli", "parent_id": None, "unit_id": 8},
            {"id": 101, "name": "Punasipuli 500g Suomi 2lk", "parent_id": 100, "unit_id": 8},
            {"id": 102, "name": "Punasipuli Lavanttila", "parent_id": 100, "unit_id": 8},
        ]
        stock = [
            {"product_id": 101, "amount": 1, "amount_opened": 0},
            {"product_id": 102, "amount": 1, "amount_opened": 0},
        ]
        self._setup(
            monkeypatch,
            ingredient={
                "id": 1, "product_id": 100, "product_name": "Punasipuli",
                "amount": 1, "specificity": "strict", "note": "",
            },
            recipe_unit_id=8,
            products=products,
            stock=stock,
        )

        detail = backend._get_recipe_detail(99)
        row = detail["ingredients"][0]
        assert row["product_id"] == 100
        assert row["specificity"] == "strict"
        assert row["status"] == "green", (
            f"Expected green (2 kpl aggregated from children ≥ 1 kpl needed), got {row['status']}"
        )

    def test_strict_on_child_does_not_aggregate_siblings(self, monkeypatch):
        """specificity=strict + matched a child (parent_id != None) → no
        aggregation, preserving the 'this exact variant only' semantic."""
        products = [
            {"id": 1, "name": "Juusto", "parent_id": None, "unit_id": 1},
            {"id": 2, "name": "Parmesan", "parent_id": 1, "unit_id": 1},
            {"id": 3, "name": "Gouda", "parent_id": 1, "unit_id": 1},
        ]
        stock = [
            # No parmesan stock; lots of gouda. Strict on parmesan must
            # NOT count gouda.
            {"product_id": 3, "amount": 500, "amount_opened": 0},
        ]
        self._setup(
            monkeypatch,
            ingredient={
                "id": 1, "product_id": 2, "product_name": "Parmesan",
                "amount": 100, "specificity": "strict", "note": "",
            },
            recipe_unit_id=1,
            products=products,
            stock=stock,
        )

        detail = backend._get_recipe_detail(99)
        row = detail["ingredients"][0]
        assert row["product_id"] == 2
        assert row["specificity"] == "strict"
        assert row["status"] == "red", (
            f"Expected red (gouda is a sibling, must not satisfy strict parmesan), got {row['status']}"
        )
