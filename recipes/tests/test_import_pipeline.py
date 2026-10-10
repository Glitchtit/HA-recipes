"""Recipe import (2.8.0): the page is summarized and extracted here, then
Storage resolves every ingredient line into a concept. The importer never
matches, creates products or guesses units itself."""
from __future__ import annotations

import pytest

import backend  # noqa: E402 — conftest stubs SDKs before this import

RECIPE = {
    "name": "Rabarberpaj",
    "servings": 6,
    "source_url": "https://example.com/rabarberpaj",
    "image_url": None,
    "instructions": ["Sätt ugnen på 200 grader."],
    "ingredients": [
        {"name": "raparperi", "specific": None, "amount": 500, "unit": "g", "note": None,
         "raw": "500 g rabarber", "source_name": "rabarber"},
        {"name": "sokeri", "specific": "hillosokeri", "amount": 1, "unit": "dl", "note": "hieno",
         "raw": "1 dl syltsocker", "source_name": "syltsocker"},
        {"name": "", "specific": None, "amount": 1, "unit": "st", "note": None,
         "raw": "1 st ???", "source_name": None},
    ],
}

RESOLVED = [
    {"index": 0, "product_id": 11, "specificity": "loose", "amount": 500, "unit_id": 1,
     "unit": "g", "created": False, "needs_review": False},
    {"index": 1, "product_id": 12, "specificity": "strict", "amount": 1, "unit_id": 4,
     "unit": "dl", "created": True, "needs_review": False},
    {"index": 2, "product_id": None, "specificity": "loose", "amount": 1, "unit_id": 9,
     "unit": "kpl", "created": False, "needs_review": True},
]


@pytest.fixture
def storage(monkeypatch):
    posts: list[tuple[str, dict | None]] = []

    def fake_post(path, data=None, **_kwargs):
        posts.append((path, data))
        if path == "ingredients/resolve":
            return {"items": RESOLVED, "created": [12]}
        if path == "recipes":
            return {"id": 7}
        return {}

    monkeypatch.setattr(backend, "_api_post", fake_post)
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: {**RECIPE, "source_url": url})
    return posts


def test_import_resolves_lines_in_storage_and_saves_them(storage):
    result = backend._handle_scrape("https://example.com/rabarberpaj")
    assert result == {"recipe_id": 7, "name": "Rabarberpaj", "skipped": ["1 st ???"]}
    paths = [p for p, _ in storage]
    assert paths[:2] == ["ingredients/resolve", "recipes"]
    sent = storage[0][1]["items"]
    assert sent[1] == {"raw": "1 dl syltsocker", "name": "sokeri", "specific": "hillosokeri",
                       "amount": 1, "unit_text": "dl", "source_name": "syltsocker"}
    body = storage[1][1]
    assert body["servings"] == 6.0 and body["source_url"] == "https://example.com/rabarberpaj"
    assert body["ingredients"] == [
        {"product_id": 11, "amount": 500.0, "unit_id": 1, "note": "raparperi", "sort_order": 0,
         "specificity": "loose", "raw_text": "500 g rabarber", "needs_review": False},
        {"product_id": 12, "amount": 1.0, "unit_id": 4, "note": "hieno — sokeri", "sort_order": 1,
         "specificity": "strict", "raw_text": "1 dl syltsocker", "needs_review": False},
    ]


def test_a_failed_resolve_saves_nothing(monkeypatch):
    posts = []

    def fake_post(path, data=None, **_kwargs):
        posts.append(path)
        if path == "ingredients/resolve":
            raise RuntimeError("Storage is down")
        return {"id": 1}

    monkeypatch.setattr(backend, "_api_post", fake_post)
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: dict(RECIPE))
    with pytest.raises(RuntimeError):
        backend._handle_scrape("https://example.com/x")
    assert "recipes" not in posts


def test_extract_prompt_asks_for_raw_lines_and_container_units(monkeypatch):
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

    monkeypatch.setattr(backend, "_call_ai_json", fake_call)
    backend._extract_recipe_from_summary("dummy", "https://example.com", None)
    prompt = captured["prompt"]
    assert '"raw"' in prompt and '"source_name"' in prompt
    for word in ("pkt", "prk", "pss", "tlk", "burk", "påse"):
        assert word in prompt, word


def test_matching_stub_density_and_scraper_code_is_gone():
    for name in ("_match_ingredient", "_ai_match_ingredients", "_deduplicate_stub_candidates",
                 "_create_child_stubs_for_unmatched_specifics", "_get_group_master_products",
                 "_ensure_density_conversions", "_ensure_units_and_conversions",
                 "_scraper_discover", "_batch_translate_to_finnish", "_translate_ingredients"):
        assert not hasattr(backend, name), name


def test_kilograms_of_a_countable_item_stay_kilograms():
    """Live finding (re-import): "1 kg peruna-sipulisekoitusta" and "1 kg
    mjölig potatis" became 1 kpl. A kilogram amount is always a small number;
    only an implausible gram amount ("1 g lime") means pieces."""
    out = backend._fix_countable_units([
        {"name": "peruna", "amount": 1, "unit": "kg"},
        {"name": "lime", "amount": 1, "unit": "g"},
    ])
    assert [i["unit"] for i in out] == ["kg", "kpl"]


def test_extract_prompt_treats_milk_fat_levels_as_plain_milk(monkeypatch):
    """Live finding (re-import): "Arla Ko® Standardmjölk" became the variant
    "Täysmaito" in one run and plain "maito" in the next. Milk fat levels are
    interchangeable in cooking, so the prompt names them as generic milk."""
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

    monkeypatch.setattr(backend, "_call_ai_json", fake_call)
    backend._extract_recipe_from_summary("dummy", "https://example.com", None)
    rule = next(line for line in captured["prompt"].splitlines() if "standardmjölk" in line)
    assert "täysmaito" in rule and "kevytmaito" in rule and "null" in rule
