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
                       "preferred": None, "amount": 1, "unit_text": "dl",
                       "source_name": "syltsocker"}
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


def _extract_with(monkeypatch, ingredients):
    monkeypatch.setattr(backend, "_call_ai_json", lambda prompt: {
        "name": "x", "servings": 4, "ingredients": ingredients, "instructions": []})
    return backend._extract_recipe_from_summary("dummy", "https://example.com", None)["ingredients"]


def test_a_to_taste_salt_and_pepper_line_becomes_two_lines(monkeypatch):
    """Live finding (re-import of Helppo juustopiirakka): "suolaa ja pippuria
    myllystä" came back as one ingredient and Storage made a "Suola ja
    pippuri" item of it. Each seasoning is its own line, same source text."""
    out = _extract_with(monkeypatch, [
        {"name": "suola ja pippuri", "specific": None, "amount": None, "unit": None,
         "note": "myllystä", "raw": "to taste suolaa ja pippuria myllystä",
         "source_name": "suolaa ja pippuria"},
    ])
    assert [(i["name"], i["source_name"], i["amount"], i["note"], i["raw"]) for i in out] == [
        ("suola", "suolaa", None, "myllystä", "to taste suolaa ja pippuria myllystä"),
        ("pippuri", "pippuria", None, "myllystä", "to taste suolaa ja pippuria myllystä"),
    ]


def test_a_list_of_seasonings_splits_on_commas_and_conjunctions(monkeypatch):
    out = _extract_with(monkeypatch, [
        {"name": "suola, pippuri ja timjami", "specific": None, "amount": 0, "unit": None,
         "note": None, "raw": "to taste salt, peppar och timjan", "source_name": "salt, peppar och timjan"},
        {"name": "salt & pepper", "specific": None, "amount": None, "unit": None,
         "note": None, "raw": "salt & pepper", "source_name": None},
    ])
    assert [i["name"] for i in out] == ["suola", "pippuri", "timjami", "salt", "pepper"]
    assert [i["source_name"] for i in out[:3]] == ["salt", "peppar", "timjan"]


def test_a_measured_line_is_never_split(monkeypatch):
    # An amount cannot be shared out between two things, and "juusto ja
    # kinkku" may be one product.
    lines = [
        {"name": "kerma ja maito", "specific": None, "amount": 2, "unit": "dl", "note": None,
         "raw": "2 dl kermaa ja maitoa", "source_name": "kermaa ja maitoa"},
        {"name": "pizza", "specific": "juusto ja kinkku -pizza", "amount": 1, "unit": "kpl", "note": None,
         "raw": "1 juusto ja kinkku -pizza", "source_name": None},
    ]
    out = _extract_with(monkeypatch, [dict(l) for l in lines])
    assert [i["name"] for i in out] == ["kerma ja maito", "pizza"]


def test_extract_prompt_asks_for_one_ingredient_per_seasoning(monkeypatch):
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

    monkeypatch.setattr(backend, "_call_ai_json", fake_call)
    backend._extract_recipe_from_summary("dummy", "https://example.com", None)
    rule = next(line for line in captured["prompt"].splitlines() if "suolaa ja pippuria" in line)
    assert "separate" in rule.lower()


def _page(yield_value):
    import json as _json
    ld = {"@context": "https://schema.org", "@type": "Recipe", "name": "Piirakka", "recipeYield": yield_value}
    return f'<html><head><script type="application/ld+json">{_json.dumps(ld)}</script></head><body>annosta</body></html>'


@pytest.mark.parametrize("value,expected", [
    ("8, 8 annosta", 8), (["4", "4 portioner"], 4), (6, 6), ("Serves 2-3", 2), ("", None), (None, None),
])
def test_servings_come_from_the_pages_recipe_data(value, expected):
    """Live finding (Helppo juustopiirakka): the page shows "8" only in a
    portion input, so the AI saw "annosta" and answered 1. schema.org
    recipeYield has the number."""
    assert backend._jsonld_servings(_page(value)) == expected


class _Soup:  # conftest stubs bs4; the scrape only needs the page text
    def __init__(self, html, _parser):
        self.html = html

    def get_text(self, separator="", strip=False):
        return self.html


def test_page_servings_win_over_the_ai_guess(monkeypatch):
    monkeypatch.setattr(backend, "_fetch_html", lambda url: _page("8, 8 annosta"))
    monkeypatch.setattr(backend, "_extract_image_url", lambda url, html=None: None)
    monkeypatch.setattr(backend, "BeautifulSoup", _Soup)
    monkeypatch.setattr(backend, "_summarize_recipe", lambda text, url: "summary")
    monkeypatch.setattr(backend, "_call_ai_json", lambda prompt: {
        "name": "Piirakka", "servings": 1, "ingredients": [], "instructions": []})
    assert backend._scrape_recipe("https://example.com/p")["servings"] == 8


def test_without_recipe_data_the_ai_servings_stay(monkeypatch):
    monkeypatch.setattr(backend, "_fetch_html", lambda url: "<html><body>4 annosta</body></html>")
    monkeypatch.setattr(backend, "_extract_image_url", lambda url, html=None: None)
    monkeypatch.setattr(backend, "BeautifulSoup", _Soup)
    monkeypatch.setattr(backend, "_summarize_recipe", lambda text, url: "summary")
    monkeypatch.setattr(backend, "_call_ai_json", lambda prompt: {
        "name": "Piirakka", "servings": 4, "ingredients": [], "instructions": []})
    assert backend._scrape_recipe("https://example.com/p")["servings"] == 4


def test_a_preferred_variant_is_forwarded_and_bound_prefer(monkeypatch):
    """"2 prk Valio ruokakermaa Kolme juustoa": the flavour reaches Storage as
    `preferred` and the 'prefer' binding is saved, not flattened to loose."""
    recipe = {**RECIPE, "ingredients": [
        {"name": "kerma", "specific": "ruokakerma", "preferred": "ruokakerma kolme juustoa",
         "amount": 2, "unit": "prk", "note": None,
         "raw": "2 prk Valio ruokakermaa Kolme juustoa", "source_name": "ruokakermaa Kolme juustoa"}]}
    resolved = [{"index": 0, "product_id": 30, "specificity": "prefer", "amount": 2,
                 "unit_id": 13, "unit": "prk", "created": True, "needs_review": False}]
    posts = []

    def fake_post(path, data=None, **_kwargs):
        posts.append((path, data))
        if path == "ingredients/resolve":
            return {"items": resolved, "created": [30]}
        return {"id": 8} if path == "recipes" else {}

    monkeypatch.setattr(backend, "_api_post", fake_post)
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: {**recipe, "source_url": url})
    backend._handle_scrape("https://example.com/vuoka")
    assert posts[0][1]["items"][0]["preferred"] == "ruokakerma kolme juustoa"
    assert posts[1][1]["ingredients"][0]["specificity"] == "prefer"


def test_extract_prompt_asks_for_a_brand_free_preferred_variant(monkeypatch):
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        return {"name": "x", "servings": 1, "ingredients": [], "instructions": []}

    monkeypatch.setattr(backend, "_call_ai_json", fake_call)
    backend._extract_recipe_from_summary("dummy", "https://example.com", None)
    prompt = captured["prompt"]
    assert '"preferred"' in prompt
    assert "Kolme juustoa" in prompt and "brand" in prompt.lower()


def test_splitting_a_combined_line_drops_the_preferred_variant():
    out = backend._split_combined([
        {"name": "suola ja pippuri", "specific": None, "preferred": "savusuola",
         "amount": None, "raw": "to taste suolaa ja pippuria", "source_name": None}])
    assert [i.get("preferred") for i in out] == [None, None]
