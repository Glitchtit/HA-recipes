"""Re-import (2.8.0): fetch a recipe's source page again and replace its
ingredients through Storage, keeping the recipe itself."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import backend  # noqa: E402 — conftest stubs SDKs before this import
from .test_import_pipeline import RECIPE, RESOLVED


@pytest.fixture
def storage(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_get(path, **_kwargs):
        calls.append(("GET", path, None))
        if path == "recipes/5":
            return {"id": 5, "name": "Pulla", "source_url": "https://example.com/pulla",
                    "picture_filename": "recipe_5_ab.jpg", "servings": 4, "ingredients": []}
        if path == "recipes/6":
            return {"id": 6, "name": "Oma", "source_url": "", "ingredients": []}
        raise AssertionError(path)

    def fake_post(path, data=None, **_kwargs):
        calls.append(("POST", path, data))
        return {"items": RESOLVED} if path == "ingredients/resolve" else {}

    def fake_put(path, data=None, **_kwargs):
        calls.append(("PUT", path, data))
        return {}

    monkeypatch.setattr(backend, "_api_get", fake_get)
    monkeypatch.setattr(backend, "_api_post", fake_post)
    monkeypatch.setattr(backend, "_api_put", fake_put)
    return calls


def test_reimport_replaces_ingredients_and_servings(storage, monkeypatch):
    fetched = []
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: fetched.append(url) or dict(RECIPE))
    result = backend._handle_reimport(5)
    assert fetched == ["https://example.com/pulla"]
    puts = [(path, data) for verb, path, data in storage if verb == "PUT"]
    assert puts[0][0] == "recipes/5/ingredients"
    assert [r["product_id"] for r in puts[0][1]["ingredients"]] == [11, 12]
    assert puts[1] == ("recipes/5", {"servings": 6.0})
    assert result == {"recipe_id": 5, "name": "Pulla", "ingredients": 2, "needs_review": 0,
                      "skipped": ["1 st ???"]}


def test_reimport_needs_a_source_url(storage, monkeypatch):
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: pytest.fail("must not fetch"))
    with pytest.raises(ValueError):
        backend._handle_reimport(6)
    assert not [c for c in storage if c[0] == "PUT"]


def test_reimport_that_finds_nothing_leaves_the_recipe_alone(storage, monkeypatch):
    monkeypatch.setattr(backend, "_scrape_recipe", lambda url: {**RECIPE, "ingredients": []})
    with pytest.raises(ValueError):
        backend._handle_reimport(5)
    assert not [c for c in storage if c[0] == "PUT"]


def test_detail_carries_raw_line_and_review_flag(monkeypatch):
    def fake_get(path, **_kwargs):
        if path == "recipes/9":
            return {"id": 9, "name": "Kastike", "description": "", "ingredients": [
                {"id": 1, "product_id": 3, "product_name": "Kerma", "amount": 2,
                 "unit_abbreviation": "dl", "note": "", "specificity": "loose",
                 "raw_text": "2 dl grädde", "needs_review": True}]}
        return {"ingredients": [{"ingredient_id": 1, "status": "green"}]}

    monkeypatch.setattr(backend, "_api_get", fake_get)
    [row] = backend._get_recipe_detail(9)["ingredients"]
    assert row["raw_text"] == "2 dl grädde" and row["needs_review"] is True


def _post(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_reimport_route_is_single_flight(monkeypatch):
    monkeypatch.setattr(backend, "_handle_reimport",
                        lambda rid: {"recipe_id": rid, "name": "Pulla", "ingredients": 2, "needs_review": 0})
    server = backend._ThreadingHTTPServer(("127.0.0.1", 0), backend._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert _post(port, "/api/recipe/5/reimport") == (
            200, {"success": True, "recipe_id": 5, "name": "Pulla", "ingredients": 2, "needs_review": 0})
        with backend._op_lock:
            status, body = _post(port, "/api/recipe/5/reimport")
        assert status == 409 and body["success"] is False
    finally:
        server.shutdown()


def test_detail_carries_the_unit_and_what_is_in_stock(monkeypatch):
    def fake_get(path, **_kwargs):
        if path == "recipes/9":
            return {"id": 9, "name": "Kastike", "description": "", "ingredients": [
                {"id": 1, "product_id": 3, "product_name": "Kerma", "amount": 2, "unit_id": 4,
                 "unit_abbreviation": "dl", "note": "", "specificity": "loose"}]}
        return {"ingredients": [{"ingredient_id": 1, "status": "yellow", "available": 1.5,
                                 "approx": True}]}

    monkeypatch.setattr(backend, "_api_get", fake_get)
    [row] = backend._get_recipe_detail(9)["ingredients"]
    assert (row["unit_id"], row["available"], row["approx"]) == (4, 1.5, True)


def test_list_carries_ingredient_status_counts(monkeypatch):
    monkeypatch.setattr(backend, "_api_get", lambda path, **_k: [
        {"id": 1, "name": "Pulla", "servings": 4, "picture_filename": None,
         "ingredient_count": 5, "missing_count": 2, "yellow_count": 1},
        {"id": 2, "name": "Vanha", "servings": 2, "picture_filename": None},
    ])
    first, older = backend._list_recipes()
    assert (first["missing_count"], first["yellow_count"], first["ingredient_count"]) == (2, 1, 5)
    assert (older["missing_count"], older["yellow_count"]) == (None, None)
