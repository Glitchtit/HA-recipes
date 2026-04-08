"""Grocy Recipes — Python backend server.

Handles recipe scraping via Gemini AI, product matching against Grocy,
missing-product discovery via grocy-scraper, and Grocy recipe CRUD.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from google import genai

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("recipe-backend")

# ---------------------------------------------------------------------------
# Configuration (from environment, set by s6-overlay run script)
# ---------------------------------------------------------------------------
GROCY_URL = os.environ.get("GROCY_BASE_URL", "").rstrip("/")
GROCY_KEY = os.environ.get("GROCY_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

PORT = 8100

# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------
_gemini_client: genai.Client | None = None

_GEMINI_MAX_RETRIES = 3


def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_KEY)
    return _gemini_client


def _call_gemini_json(prompt: str) -> dict | list | None:
    """Call Gemini and parse the response as JSON with retries."""
    client = _get_gemini()
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = resp.text or ""
            # Strip control chars that sometimes appear
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            return json.loads(text)
        except Exception as exc:
            log.warning("Gemini attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Grocy API helpers
# ---------------------------------------------------------------------------
_grocy_session: requests.Session | None = None


def _grocy() -> requests.Session:
    global _grocy_session
    if _grocy_session is None:
        _grocy_session = requests.Session()
        _grocy_session.headers.update({"GROCY-API-KEY": GROCY_KEY})
    return _grocy_session


def _grocy_get(path: str) -> Any:
    r = _grocy().get(f"{GROCY_URL}/api/{path}")
    r.raise_for_status()
    return r.json()


def _grocy_post(path: str, data: dict) -> Any:
    r = _grocy().post(f"{GROCY_URL}/api/{path}", json=data)
    r.raise_for_status()
    return r.json() if r.content else {}


def _grocy_put(path: str, data: dict) -> None:
    r = _grocy().put(f"{GROCY_URL}/api/{path}", json=data)
    r.raise_for_status()


def _grocy_delete(path: str) -> None:
    r = _grocy().delete(f"{GROCY_URL}/api/{path}")
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Scraper proxy
# ---------------------------------------------------------------------------
def _scraper_available() -> bool:
    """Check if grocy-scraper addon is reachable (via nginx proxy)."""
    try:
        r = requests.get(f"http://127.0.0.1:8099/api/scraper/config", timeout=3)
        return r.ok
    except Exception:
        return False


def _translate_to_finnish_search(ingredient_name: str) -> str:
    """Translate an ingredient name to a Finnish grocery search term using Gemini.

    The scraper searches Finnish grocery sites, so we need Finnish terms.
    """
    prompt = f"""Translate this ingredient name to a short Finnish grocery search term.

Ingredient: "{ingredient_name}"

Return a JSON object: {{"search_term": "finnish search term"}}

Rules:
- Return a simple Finnish word suitable for searching a Finnish grocery store website (k-ruoka.fi).
- Use common Finnish grocery terms, e.g.: "torskfilé" → "turska", "butter" → "voi", "cream" → "kerma", "chicken breast" → "kananrinta", "bread crumbs" → "korppujauho"
- Keep it short — 1-2 words maximum. Just the product type, no brands or quantities.
- If already in Finnish, return as-is."""

    result = _call_gemini_json(prompt)
    if result and isinstance(result, dict) and result.get("search_term"):
        return result["search_term"]
    return ingredient_name


def _scraper_discover(product_name: str) -> dict | None:
    """Ask grocy-scraper to find and create a product by name search.

    Translates the ingredient name to a Finnish grocery search term first,
    since the scraper searches Finnish grocery sites (k-ruoka.fi, s-kaupat.fi).
    """
    # Translate to a Finnish grocery search term
    search_term = _translate_to_finnish_search(product_name)
    log.info("Scraper search: '%s' → Finnish search term: '%s'", product_name, search_term)

    try:
        r = requests.post(
            "http://127.0.0.1:8099/api/scraper/search",
            json={"query": search_term, "max_products": 3},
            timeout=30,
        )
        if not r.ok:
            return None
        results = r.json()
        products = results.get("products", [])
        if not products:
            return None

        # Add the first matching product
        r = requests.post(
            "http://127.0.0.1:8099/api/scraper/add_products",
            json={"products": [products[0]]},
            timeout=60,
        )
        if not r.ok:
            return None
        data = r.json()
        if data.get("success") and data.get("added", 0) > 0:
            log.info("Scraper created product for '%s'", product_name)
            return data
        return None
    except Exception as exc:
        log.warning("Scraper discover failed for '%s': %s", product_name, exc)
        return None


# ---------------------------------------------------------------------------
# Recipe scraping (Gemini AI)
# ---------------------------------------------------------------------------
def _fetch_page(url: str) -> str:
    """Fetch a web page and return cleaned text content."""
    r = requests.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (compatible; GrocyRecipes/1.0)"
    })
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # Remove script/style tags
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:15000]


def _extract_image_url(url: str, html: str | None = None) -> str | None:
    """Try to extract the main recipe image from the page."""
    try:
        if html is None:
            r = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GrocyRecipes/1.0)"
            })
            html = r.text
        soup = BeautifulSoup(html, "html.parser")
        # Try og:image first
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
        # Try schema.org recipe image
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                if isinstance(ld, list):
                    ld = ld[0]
                if ld.get("@type") == "Recipe" and ld.get("image"):
                    img = ld["image"]
                    if isinstance(img, list):
                        img = img[0]
                    if isinstance(img, dict):
                        img = img.get("url", "")
                    return img if img else None
            except Exception:
                continue
        return None
    except Exception:
        return None


def _scrape_recipe(url: str) -> dict:
    """Scrape a recipe from URL using Gemini AI.

    Returns: {name, image_url, servings, source_url, ingredients: [{name, amount, unit, note}], instructions: [str]}
    """
    # Fetch the page
    r = requests.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (compatible; GrocyRecipes/1.0)"
    })
    r.raise_for_status()
    raw_html = r.text
    page_text = BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)[:15000]

    image_url = _extract_image_url(url, raw_html)

    prompt = f"""Analyze this recipe page and extract the recipe as structured JSON.

Page URL: {url}

Page content:
{page_text}

Return a JSON object with exactly these fields:
{{
  "name": "Recipe name (in the original language of the recipe)",
  "servings": <number of servings as integer>,
  "ingredients": [
    {{"name": "ingredient name ALWAYS IN FINNISH", "amount": <number or null>, "unit": "unit string or null", "note": "any note like 'chopped' or null"}}
  ],
  "instructions": ["Step 1 text", "Step 2 text", ...]
}}

CRITICAL LANGUAGE RULES:
- The recipe may be in Swedish, English, Finnish, or any other language.
- ALWAYS translate ingredient names to Finnish, regardless of the recipe language.
  Examples: "smör" (Swedish) → "voi", "butter" (English) → "voi", "torskfilé" → "turskafile", "salt" → "suola", "milk" → "maito", "ägg" → "kananmuna", "flour" → "vehnäjauho", "potatis" → "peruna"
- Keep ingredient names as simple generic product names (e.g. "kananmuna" not "3 kananmunaa", "voi" not "Valio voi 500g")
- The recipe name and instructions should stay in the original language of the recipe.

Other rules:
- Amount should be a number (float), not a string
- Unit should be a standard abbreviation (dl, ml, l, g, kg, kpl, tl, rkl, rs) or null for count items
- Instructions should be clear numbered steps
- Do NOT include any text outside the JSON object"""

    result = _call_gemini_json(prompt)
    if not result or not isinstance(result, dict):
        raise ValueError("Failed to extract recipe from page")

    result["source_url"] = url
    result["image_url"] = image_url
    return result


# ---------------------------------------------------------------------------
# Product matching
# ---------------------------------------------------------------------------
def _get_all_products() -> list[dict]:
    """Get all Grocy products."""
    return _grocy_get("objects/products")


def _match_ingredient(name: str, products: list[dict]) -> dict | None:
    """Find the best matching Grocy product for an ingredient name.

    Only uses exact match. Substring matching is intentionally avoided to
    prevent false positives like "salt" → "Lay's Chips Salted".
    Prefers parent products (products that other products reference as parent).
    """
    name_lower = name.lower().strip()

    parent_ids = {
        int(p["parent_product_id"])
        for p in products
        if p.get("parent_product_id")
    }

    # Exact name match only
    for p in products:
        if p["name"].lower().strip() == name_lower:
            # If this product has a parent, prefer the parent
            if p.get("parent_product_id"):
                parent = next(
                    (pp for pp in products if pp["id"] == int(p["parent_product_id"])),
                    None,
                )
                if parent:
                    return parent
            return p

    return None


def _ai_match_ingredients(
    ingredients: list[dict], products: list[dict]
) -> list[dict]:
    """Use Gemini AI to match ingredients to Grocy products when simple matching fails."""
    unmatched = [i for i in ingredients if i.get("_product_id") is None]
    if not unmatched:
        return ingredients

    product_names = [
        {"id": p["id"], "name": p["name"], "is_parent": p["id"] in {
            int(pp["parent_product_id"])
            for pp in products
            if pp.get("parent_product_id")
        }}
        for p in products
    ]

    ingredient_list = json.dumps(
        [{"index": i, "name": ing["name"]} for i, ing in enumerate(unmatched)],
        ensure_ascii=False,
    )
    product_list = json.dumps(product_names[:500], ensure_ascii=False)

    prompt = f"""Match these recipe ingredients to the closest Grocy product.

IMPORTANT CONTEXT:
- This household speaks Swedish, Finnish, and English.
- Recipes may be in ANY of these languages.
- ALL Grocy product names are in Finnish.
- Ingredient names below have been translated to Finnish, but may still have slight variations.

Ingredients to match:
{ingredient_list}

Available Grocy products (prefer products where is_parent=true):
{product_list}

Return a JSON array of objects:
[{{"index": 0, "product_id": <matched product ID or null if no match>, "confidence": "high"|"medium"|"low"}}]

MATCHING RULES:
- Match by ingredient TYPE and MEANING, not by brand name or substring.
  Example: "suola" (salt) should match a parent product "Suola" — NOT "Lay's Chips Salted" or any chip/crisp product.
- "voi" (butter) should match "Voi" parent product — NOT "Voileipäkeksi" (sandwich cookie).
- Prefer parent products (is_parent=true) over specific product variants.
- A parent product represents a general category (e.g. "Maito" = any milk, "Voi" = any butter).
- Only match with "high" or "medium" confidence — set product_id to null for poor or uncertain matches.
- Do NOT match based on a word appearing inside a brand name or product description.
- If the ingredient is a basic staple (suola, pippuri, sokeri, voi, maito, jauho), look for the generic parent product."""

    result = _call_gemini_json(prompt)
    if not result or not isinstance(result, list):
        return ingredients

    for match in result:
        idx = match.get("index")
        pid = match.get("product_id")
        conf = match.get("confidence", "low")
        if idx is not None and pid is not None and conf in ("high", "medium"):
            if 0 <= idx < len(unmatched):
                unmatched[idx]["_product_id"] = pid
                unmatched[idx]["_match_confidence"] = conf

    return ingredients


# ---------------------------------------------------------------------------
# Recipe CRUD in Grocy
# ---------------------------------------------------------------------------
def _upload_recipe_image(recipe_id: int, image_url: str) -> str | None:
    """Download image from URL and upload to Grocy."""
    try:
        r = requests.get(image_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GrocyRecipes/1.0)"
        })
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "image/jpeg")
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"recipe_{recipe_id}.{ext}"
        encoded = base64.b64encode(filename.encode()).decode()

        upload_r = _grocy().put(
            f"{GROCY_URL}/api/files/recipepictures/{encoded}",
            data=r.content,
            headers={
                "GROCY-API-KEY": GROCY_KEY,
                "Content-Type": "application/octet-stream",
            },
        )
        upload_r.raise_for_status()
        return filename
    except Exception as exc:
        log.warning("Failed to upload recipe image: %s", exc)
        return None


def _create_recipe_in_grocy(recipe_data: dict, matched_ingredients: list[dict]) -> dict:
    """Create a recipe in Grocy with all its ingredients.

    Returns the created recipe with Grocy IDs.
    """
    # Create the recipe shell
    recipe_body = {
        "name": recipe_data["name"],
        "description": "\n".join(recipe_data.get("instructions", [])),
        "base_servings": recipe_data.get("servings", 4),
    }
    if recipe_data.get("source_url"):
        recipe_body["description"] = (
            f"Source: {recipe_data['source_url']}\n\n{recipe_body['description']}"
        )

    resp = _grocy_post("objects/recipes", recipe_body)
    recipe_id = resp.get("created_object_id")
    if not recipe_id:
        raise ValueError("Failed to create recipe in Grocy")

    log.info("Created recipe '%s' (ID %d)", recipe_data["name"], recipe_id)

    # Upload image if available
    if recipe_data.get("image_url"):
        filename = _upload_recipe_image(recipe_id, recipe_data["image_url"])
        if filename:
            _grocy_put(f"objects/recipes/{recipe_id}", {"picture_file_name": filename})
            log.info("Uploaded recipe image: %s", filename)

    # Get the default quantity unit (piece)
    qu_id = 1  # Default to piece
    try:
        units = _grocy_get("objects/quantity_units")
        for u in units:
            if u["name"].lower() in ("kpl", "piece", "stück"):
                qu_id = u["id"]
                break
    except Exception:
        pass

    # Create ingredient positions
    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        if not pid:
            continue

        pos_body: dict[str, Any] = {
            "recipe_id": recipe_id,
            "product_id": pid,
            "amount": ing.get("amount") or 1,
            "qu_id": qu_id,
        }
        note_parts = []
        if ing.get("unit"):
            note_parts.append(ing["unit"])
        if ing.get("note"):
            note_parts.append(ing["note"])
        if ing.get("name"):
            note_parts.append(ing["name"])
        if note_parts:
            pos_body["note"] = " — ".join(note_parts)

        try:
            _grocy_post("objects/recipes_pos", pos_body)
        except Exception as exc:
            log.warning("Failed to add ingredient %s: %s", ing.get("name"), exc)

    return {"recipe_id": recipe_id, "name": recipe_data["name"]}


def _list_recipes() -> list[dict]:
    """List all recipes from Grocy with their images."""
    recipes = _grocy_get("objects/recipes")
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "picture_file_name": r.get("picture_file_name"),
            "base_servings": r.get("base_servings", 1),
        }
        for r in recipes
    ]


def _get_recipe_detail(recipe_id: int) -> dict:
    """Get full recipe detail with ingredient stock status."""
    recipe = _grocy_get(f"objects/recipes/{recipe_id}")

    # Get all recipe ingredients
    all_positions = _grocy_get("objects/recipes_pos")
    positions = [p for p in all_positions if p.get("recipe_id") == recipe_id]

    # Get stock info
    stock = _grocy_get("stock")
    stock_by_product: dict[int, dict] = {}
    for s in stock:
        stock_by_product[s["product_id"]] = s

    # Get all products for name lookups
    products_list = _grocy_get("objects/products")
    products_by_id = {p["id"]: p for p in products_list}

    ingredients = []
    for pos in positions:
        pid = pos.get("product_id")
        product = products_by_id.get(pid, {})
        product_name = product.get("name", f"Product #{pid}")
        needed = pos.get("amount", 1)

        stock_entry = stock_by_product.get(pid)
        in_stock = 0
        amount_opened = 0
        if stock_entry:
            in_stock = stock_entry.get("amount", 0)
            amount_opened = stock_entry.get("amount_opened", 0)

        # Determine status: green, yellow, red
        if in_stock >= needed:
            if in_stock == 1 and amount_opened >= 1:
                status = "yellow"
            else:
                status = "green"
        else:
            status = "red"

        # Get parent product info
        parent_id = product.get("parent_product_id")
        parent_name = None
        if parent_id:
            parent = products_by_id.get(int(parent_id))
            if parent:
                parent_name = parent.get("name")

        ingredients.append({
            "id": pos.get("id"),
            "product_id": pid,
            "product_name": product_name,
            "parent_product_id": parent_id,
            "parent_product_name": parent_name,
            "amount_needed": needed,
            "amount_in_stock": in_stock,
            "amount_opened": amount_opened,
            "unit": pos.get("note", ""),
            "status": status,
        })

    # Parse instructions from description
    description = recipe.get("description", "")
    source_url = None
    instructions = []
    for line in description.split("\n"):
        line = line.strip()
        if line.startswith("Source: "):
            source_url = line[8:].strip()
        elif line:
            instructions.append(line)

    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "picture_file_name": recipe.get("picture_file_name"),
        "base_servings": recipe.get("base_servings", 1),
        "source_url": source_url,
        "ingredients": ingredients,
        "instructions": instructions,
    }


def _add_to_shopping_list(recipe_id: int, mode: str) -> dict:
    """Add recipe ingredients to Grocy shopping list.

    mode: "missing" | "all" | "missing_and_opened"
    """
    detail = _get_recipe_detail(recipe_id)
    added = 0

    for ing in detail["ingredients"]:
        should_add = False
        if mode == "all":
            should_add = True
        elif mode == "missing":
            should_add = ing["status"] == "red"
        elif mode == "missing_and_opened":
            should_add = ing["status"] in ("red", "yellow")

        if not should_add:
            continue

        # Prefer parent product for shopping list
        pid = ing.get("parent_product_id") or ing.get("product_id")
        if not pid:
            continue
        pid = int(pid)

        try:
            _grocy_post("stock/shoppinglist/add-product", {
                "product_id": pid,
                "list_id": 1,
                "product_amount": 1,
            })
            added += 1
        except Exception as exc:
            log.warning("Failed to add product %d to shopping list: %s", pid, exc)

    return {"added": added, "mode": mode}


def _delete_recipe(recipe_id: int) -> None:
    """Delete a recipe and its ingredient positions from Grocy."""
    # Delete positions first
    all_positions = _grocy_get("objects/recipes_pos")
    for pos in all_positions:
        if pos.get("recipe_id") == recipe_id:
            try:
                _grocy_delete(f"objects/recipes_pos/{pos['id']}")
            except Exception:
                pass

    _grocy_delete(f"objects/recipes/{recipe_id}")
    log.info("Deleted recipe ID %d", recipe_id)


# ---------------------------------------------------------------------------
# Full scrape pipeline
# ---------------------------------------------------------------------------
def _handle_scrape(url: str) -> dict:
    """Full pipeline: scrape URL → match products → discover missing → save to Grocy."""
    log.info("Scraping recipe from: %s", url)

    # 1. Scrape the recipe
    recipe_data = _scrape_recipe(url)
    log.info(
        "Extracted recipe: '%s' with %d ingredients",
        recipe_data.get("name"),
        len(recipe_data.get("ingredients", [])),
    )

    # 2. Get all Grocy products
    products = _get_all_products()

    # 3. Match ingredients to products
    for ing in recipe_data.get("ingredients", []):
        match = _match_ingredient(ing["name"], products)
        if match:
            ing["_product_id"] = match["id"]
            log.info("Matched '%s' → '%s' (ID %d)", ing["name"], match["name"], match["id"])
        else:
            ing["_product_id"] = None

    # 4. AI-assisted matching for unmatched
    recipe_data["ingredients"] = _ai_match_ingredients(
        recipe_data.get("ingredients", []), products
    )

    # 5. Discover missing products via scraper
    unmatched = [
        i for i in recipe_data.get("ingredients", [])
        if i.get("_product_id") is None
    ]

    if unmatched:
        if not _scraper_available():
            raise RuntimeError(
                "Scraper unavailable, try again later. "
                f"{len(unmatched)} ingredient(s) could not be matched: "
                + ", ".join(i["name"] for i in unmatched)
            )

        for ing in unmatched:
            result = _scraper_discover(ing["name"])
            if result:
                # Refresh products to find the newly created one
                products = _get_all_products()
                match = _match_ingredient(ing["name"], products)
                if match:
                    ing["_product_id"] = match["id"]
                    log.info(
                        "Discovered and matched '%s' → '%s' (ID %d)",
                        ing["name"], match["name"], match["id"],
                    )

        # Re-run AI matching for any still-unmatched after discover
        still_unmatched_after_discover = [
            i for i in recipe_data.get("ingredients", [])
            if i.get("_product_id") is None
        ]
        if still_unmatched_after_discover:
            products = _get_all_products()
            recipe_data["ingredients"] = _ai_match_ingredients(
                recipe_data.get("ingredients", []), products
            )

    # 6. Create stub parent products for any still-unmatched ingredients
    still_unmatched = [
        i for i in recipe_data.get("ingredients", [])
        if i.get("_product_id") is None
    ]
    for ing in still_unmatched:
        stub_name = ing["name"]
        log.warning(
            "No existing product found for '%s' — creating stub parent product",
            stub_name,
        )
        try:
            resp = _grocy_post("objects/products", {
                "name": stub_name,
                "description": "Auto-created by recipe scraper",
                "location_id": 1,
                "qu_id_purchase": 1,
                "qu_id_stock": 1,
                "qu_factor_purchase_to_stock": 1,
                "min_stock_amount": 0,
            })
            new_id = resp.get("created_object_id")
            if new_id:
                ing["_product_id"] = new_id
                log.info(
                    "Created stub product '%s' (ID %d)", stub_name, new_id,
                )
        except Exception as exc:
            log.warning("Failed to create stub product '%s': %s", stub_name, exc)

    # 7. Create recipe in Grocy
    result = _create_recipe_in_grocy(recipe_data, recipe_data["ingredients"])
    return result


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
_op_lock = threading.Lock()


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)

    def handle(self) -> None:
        try:
            super().handle()
        except BrokenPipeError:
            pass

    # ── GET ────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = self.path.rstrip("/")

        if path in ("/api/config", "api/config"):
            return self._json({"configured": bool(GROCY_URL and GROCY_KEY and GEMINI_KEY)})

        if path == "/api/recipes":
            try:
                recipes = _list_recipes()
                return self._json({"success": True, "recipes": recipes})
            except Exception as exc:
                return self._json({"success": False, "error": str(exc)}, 500)

        # Recipe detail: /api/recipe/<id>
        m = re.match(r"/api/recipe/(\d+)$", path)
        if m:
            try:
                detail = _get_recipe_detail(int(m.group(1)))
                return self._json({"success": True, "recipe": detail})
            except Exception as exc:
                return self._json({"success": False, "error": str(exc)}, 500)

        self._json({"error": "Not found"}, 404)

    # ── POST ───────────────────────────────────────────────────────────
    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        body = self._read_body()

        if path == "/api/recipe/scrape":
            url = (body or {}).get("url", "").strip()
            if not url:
                return self._json({"success": False, "error": "URL is required"}, 400)
            if not _op_lock.acquire(blocking=False):
                return self._json(
                    {"success": False, "error": "Another operation is in progress"},
                    409,
                )
            try:
                result = _handle_scrape(url)
                return self._json({"success": True, **result})
            except Exception as exc:
                log.exception("Scrape failed")
                return self._json({"success": False, "error": str(exc)}, 500)
            finally:
                _op_lock.release()

        # Shopping list: /api/recipe/<id>/shopping-list
        m = re.match(r"/api/recipe/(\d+)/shopping-list$", path)
        if m:
            mode = (body or {}).get("mode", "missing")
            try:
                result = _add_to_shopping_list(int(m.group(1)), mode)
                return self._json({"success": True, **result})
            except Exception as exc:
                return self._json({"success": False, "error": str(exc)}, 500)

        self._json({"error": "Not found"}, 404)

    # ── DELETE ─────────────────────────────────────────────────────────
    def do_DELETE(self) -> None:
        path = self.path.rstrip("/")
        m = re.match(r"/api/recipe/(\d+)$", path)
        if m:
            try:
                _delete_recipe(int(m.group(1)))
                return self._json({"success": True})
            except Exception as exc:
                return self._json({"success": False, "error": str(exc)}, 500)

        self._json({"error": "Not found"}, 404)

    # ── Helpers ────────────────────────────────────────────────────────
    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return None

    def _json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    log.info("Starting recipe backend on port %d", PORT)
    log.info("Grocy URL: %s", GROCY_URL)
    log.info("Gemini model: %s", GEMINI_MODEL)

    server = _ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
