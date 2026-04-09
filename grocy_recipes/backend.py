"""Grocy Recipes — Python backend server.

Handles recipe scraping via Gemini AI, product matching against Grocy,
missing-product discovery via grocy-scraper, and Grocy recipe CRUD.
"""

from __future__ import annotations

import base64
import json
import logging
import math
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
_DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
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
        _gemini_client = genai.Client(
            api_key=GEMINI_KEY,
            http_options={"timeout": 120_000},
        )
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
# Quantity unit management
# ---------------------------------------------------------------------------

# Standard recipe units with Finnish names
_STANDARD_UNITS = [
    {"name": "Gramma", "name_plural": "Grammaa", "description": "g"},
    {"name": "Kilogramma", "name_plural": "Kilogrammaa", "description": "kg"},
    {"name": "Millilitra", "name_plural": "Millilitraa", "description": "ml"},
    {"name": "Desilitra", "name_plural": "Desilitraa", "description": "dl"},
    {"name": "Litra", "name_plural": "Litraa", "description": "l"},
    {"name": "Teelusikka", "name_plural": "Teelusikkaa", "description": "tl"},
    {"name": "Ruokalusikka", "name_plural": "Ruokalusikkaa", "description": "rkl"},
    {"name": "Ripaus", "name_plural": "Ripausta", "description": "rs"},
    {"name": "Kappale", "name_plural": "Kappaletta", "description": "kpl"},
]

# Global conversions between compatible units: (from_abbrev, to_abbrev, factor)
# "1 <from> = <factor> <to>"
_GLOBAL_CONVERSIONS = [
    ("kg", "g", 1000),
    ("l", "dl", 10),
    ("l", "ml", 1000),
    ("dl", "ml", 100),
    ("rkl", "ml", 15),
    ("tl", "ml", 5),
]

# Map common unit string variations to canonical abbreviation
_UNIT_ALIASES: dict[str, str] = {
    "g": "g", "gr": "g", "gram": "g", "gramma": "g",
    "kg": "kg", "kilo": "kg", "kilogramma": "kg",
    "ml": "ml", "millilitra": "ml",
    "dl": "dl", "desilitra": "dl",
    "l": "l", "litra": "l",
    "tl": "tl", "teelusikka": "tl",
    "rkl": "rkl", "ruokalusikka": "rkl",
    "rs": "rs", "ripaus": "rs",
    "kpl": "kpl", "kappale": "kpl", "pcs": "kpl", "piece": "kpl", "st": "kpl",
}

# Unit domain sets for cross-domain detection
_WEIGHT_UNITS = {"g", "kg"}
_VOLUME_UNITS = {"ml", "dl", "l", "tl", "rkl"}

# Normalisation factors (relative to base: kg for weight, l for volume)
_WEIGHT_FACTORS = {"kg": 1.0, "g": 1000.0}  # 1 kg = 1000 g
_VOLUME_FACTORS = {"l": 1.0, "dl": 10.0, "ml": 1000.0}  # 1 l = 10 dl = 1000 ml

# Cache: abbreviation → Grocy QU ID
_unit_map: dict[str, int] | None = None
_unit_map_lock = threading.Lock()


def _ensure_units_and_conversions() -> dict[str, int]:
    """Ensure standard recipe units and global conversions exist in Grocy.

    Returns a mapping of unit abbreviation → Grocy QU ID.
    Idempotent — skips units/conversions that already exist.
    """
    global _unit_map
    with _unit_map_lock:
        if _unit_map is not None:
            return _unit_map

        existing_units = _grocy_get("objects/quantity_units")
        existing_by_desc = {}
        existing_by_name = {}
        for u in existing_units:
            if u.get("description"):
                existing_by_desc[u["description"].lower().strip()] = u["id"]
            if u.get("name"):
                existing_by_name[u["name"].lower().strip()] = u["id"]

        abbrev_to_id: dict[str, int] = {}

        for unit_def in _STANDARD_UNITS:
            abbrev = unit_def["description"]
            # Check if unit already exists (by description/abbreviation or name)
            uid = existing_by_desc.get(abbrev.lower())
            if uid is None:
                uid = existing_by_name.get(unit_def["name"].lower())
            if uid is None:
                try:
                    resp = _grocy_post("objects/quantity_units", unit_def)
                    uid = int(resp.get("created_object_id", 0))
                    log.debug("Created QU '%s' (ID %d)", unit_def["name"], uid)
                except Exception as exc:
                    log.warning("Failed to create QU '%s': %s", unit_def["name"], exc)
                    continue
            abbrev_to_id[abbrev] = uid

        # Also map the "Piece"/"Pack" defaults if they exist
        for u in existing_units:
            name_lower = (u.get("name") or "").lower().strip()
            if name_lower in ("piece", "pack", "stück"):
                abbrev_to_id.setdefault("piece", u["id"])

        # Create global conversions
        existing_conversions = _grocy_get("objects/quantity_unit_conversions")
        conv_set = set()
        for c in existing_conversions:
            if c.get("product_id") is None or c.get("product_id") == "":
                conv_set.add((int(c["from_qu_id"]), int(c["to_qu_id"])))

        for from_abbrev, to_abbrev, factor in _GLOBAL_CONVERSIONS:
            from_id = abbrev_to_id.get(from_abbrev)
            to_id = abbrev_to_id.get(to_abbrev)
            if from_id is None or to_id is None:
                continue
            if (from_id, to_id) in conv_set:
                continue
            try:
                _grocy_post("objects/quantity_unit_conversions", {
                    "from_qu_id": from_id,
                    "to_qu_id": to_id,
                    "factor": factor,
                })
                log.debug("Created global conversion: 1 %s = %s %s", from_abbrev, factor, to_abbrev)
            except Exception as exc:
                log.warning("Failed to create conversion %s→%s: %s", from_abbrev, to_abbrev, exc)

        _unit_map = abbrev_to_id
        log.debug("Unit map initialised: %s", {k: v for k, v in abbrev_to_id.items()})
        return _unit_map


def _get_unit_map() -> dict[str, int]:
    """Get the cached unit abbreviation → QU ID mapping, initialising if needed."""
    if _unit_map is not None:
        return _unit_map
    return _ensure_units_and_conversions()


def _resolve_unit_id(unit_str: str | None) -> int | None:
    """Resolve a unit string (e.g. 'dl', 'gram', 'l') to a Grocy QU ID.

    Returns None for count/piece units ('kpl') so the caller falls back
    to the product's stock QU — which is already the count unit.
    """
    if not unit_str:
        return None
    canonical = _UNIT_ALIASES.get(unit_str.lower().strip())
    if canonical is None or canonical == "kpl":
        return None
    umap = _get_unit_map()
    return umap.get(canonical)


def _canonical_abbrev(unit_str: str | None) -> str | None:
    """Normalise a unit string to its canonical abbreviation."""
    if not unit_str:
        return None
    return _UNIT_ALIASES.get(unit_str.lower().strip())


def _derive_density_conversions(
    from_unit: str, to_unit: str, factor: float,
) -> list[tuple[str, str, float]]:
    """Compute all cross-domain weight↔volume pairs from one primary density conversion.

    Given e.g. ``("kg", "l", 1.67)`` → produces pairs like
    ``("kg", "dl", 16.7)``, ``("g", "l", 0.00167)``, etc.
    Excludes the primary pair itself.
    """
    if from_unit in _WEIGHT_FACTORS and to_unit in _VOLUME_FACTORS:
        kg_to_l = factor * _WEIGHT_FACTORS[from_unit] / _VOLUME_FACTORS[to_unit]
    elif from_unit in _VOLUME_FACTORS and to_unit in _WEIGHT_FACTORS:
        kg_to_l = _VOLUME_FACTORS[from_unit] / (factor * _WEIGHT_FACTORS[to_unit])
    else:
        return []

    derived: list[tuple[str, str, float]] = []
    for w, w_f in _WEIGHT_FACTORS.items():
        for v, v_f in _VOLUME_FACTORS.items():
            if w == from_unit and v == to_unit:
                continue  # skip the primary pair
            d_factor = round(kg_to_l * v_f / w_f, 6)
            if d_factor > 0:
                derived.append((w, v, d_factor))
    return derived


def _create_product_conversions(
    matched_ingredients: list[dict], products_by_id: dict[int, dict]
) -> None:
    """Use Gemini AI to determine product package sizes and create conversions.

    For each matched product, analyse the product name to determine the package
    size (e.g. "Arla Kevytmaito 1L" → 1 piece = 1 litre) and create a
    product-specific quantity unit conversion in Grocy.
    """
    umap = _get_unit_map()
    if not umap:
        return

    # Collect products that need conversions
    products_to_check = []
    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        recipe_unit = _canonical_abbrev(ing.get("unit"))
        if pid is None or recipe_unit is None or recipe_unit == "kpl":
            continue
        prod = products_by_id.get(int(pid), {})
        products_to_check.append({
            "product_id": int(pid),
            "product_name": prod.get("name", ""),
            "recipe_unit": recipe_unit,
        })

    if not products_to_check:
        return

    # Check which products already have conversions
    existing_conversions = _grocy_get("objects/quantity_unit_conversions")
    products_with_conv: set[int] = set()
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is not None and cpid != "":
            products_with_conv.add(int(cpid))

    need_conv = [p for p in products_to_check if p["product_id"] not in products_with_conv]
    if not need_conv:
        return

    # Deduplicate by product_id
    seen_pids: set[int] = set()
    unique_need: list[dict] = []
    for p in need_conv:
        if p["product_id"] not in seen_pids:
            seen_pids.add(p["product_id"])
            unique_need.append(p)

    product_list = json.dumps(
        [{"product_id": p["product_id"], "name": p["product_name"]} for p in unique_need],
        ensure_ascii=False,
    )

    prompt = f"""Analyse these Finnish grocery product names and determine the package size for each.

Products:
{product_list}

For each product, determine:
1. The quantity in the package (e.g. "Arla Kevytmaito 1L" → amount: 1, unit: "l")
2. The unit of measurement (g, kg, ml, dl, l)

Return a JSON array:
[{{"product_id": <id>, "amount": <number>, "unit": "g"|"kg"|"ml"|"dl"|"l"|null}}]

RULES:
- Look for size indicators in the product name (e.g. "1L", "500g", "2kg", "200ml")
- Finnish products commonly use: g, kg, dl, l, ml
- If the name contains NO size information, return unit: null
- Common Finnish package sizes: milk 1L, flour 2kg, butter 500g, cream 2dl
- "tölkki" / "tlk" usually means a can (330ml for drinks, 400ml/400g for canned goods)
- Be precise — "500g" means amount: 500, unit: "g" — NOT amount: 0.5, unit: "kg"
- If multiple sizes appear, use the LAST/most specific one"""

    result = _call_gemini_json(prompt)
    if not result or not isinstance(result, list):
        log.warning("Gemini failed to determine product package sizes")
        return

    piece_id = umap.get("piece") or umap.get("kpl")
    if piece_id is None:
        # Try to find a "Piece" unit from existing units
        all_units = _grocy_get("objects/quantity_units")
        for u in all_units:
            name_lower = (u.get("name") or "").lower()
            if name_lower in ("piece", "pack", "kappale", "stück"):
                piece_id = u["id"]
                break
    if piece_id is None:
        log.warning("Cannot create product conversions — no Piece unit found")
        return

    for item in result:
        pid = item.get("product_id")
        amount = item.get("amount")
        unit_abbrev = item.get("unit")
        if pid is None or amount is None or unit_abbrev is None:
            continue

        to_qu_id = umap.get(unit_abbrev)
        if to_qu_id is None:
            continue

        try:
            _grocy_post("objects/quantity_unit_conversions", {
                "from_qu_id": piece_id,
                "to_qu_id": to_qu_id,
                "factor": float(amount),
                "product_id": int(pid),
            })
            log.debug(
                "Created conversion for product %d: 1 piece = %s %s",
                pid, amount, unit_abbrev,
            )
        except Exception as exc:
            log.warning("Failed to create conversion for product %d: %s", pid, exc)


def _update_product_default_units(
    matched_ingredients: list[dict], products_by_id: dict[int, dict]
) -> None:
    """Update product default QUs to match recipe units when no conversion exists.

    For products where the recipe uses a measurable unit (g, dl, etc.) but the
    product's stock QU is still the generic Piece and no product-specific
    conversion was created by AI, change the product's default units to the
    recipe unit.  E.g. "Turskafile" stock QU → grams.
    """
    umap = _get_unit_map()
    if not umap:
        return

    existing_conversions = _grocy_get("objects/quantity_unit_conversions")
    products_with_conv: set[int] = set()
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is not None and cpid != "":
            products_with_conv.add(int(cpid))

    updated: set[int] = set()
    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        recipe_unit = _canonical_abbrev(ing.get("unit"))
        if pid is None or recipe_unit is None or recipe_unit == "kpl":
            continue
        pid = int(pid)
        if pid in updated or pid in products_with_conv:
            continue

        recipe_qu_id = umap.get(recipe_unit)
        if recipe_qu_id is None:
            continue

        prod = products_by_id.get(pid, {})
        stock_qu_id = prod.get("qu_id_stock")
        if stock_qu_id == recipe_qu_id:
            continue

        try:
            _grocy_put(f"objects/products/{pid}", {
                "qu_id_stock": recipe_qu_id,
                "qu_id_purchase": recipe_qu_id,
                "qu_id_consume": recipe_qu_id,
                "qu_id_price": recipe_qu_id,
            })
            updated.add(pid)
            log.debug(
                "Updated product %d (%s) default unit to %s",
                pid, prod.get("name", ""), recipe_unit,
            )
        except Exception as exc:
            log.warning("Failed to update product %d default unit: %s", pid, exc)


def _ensure_density_conversions(
    matched_ingredients: list[dict], products_by_id: dict[int, dict]
) -> None:
    """Create cross-domain (weight↔volume) density conversions for products.

    For each ingredient whose recipe unit is in a different domain (weight vs
    volume) than the product's existing conversions, use Gemini AI to estimate
    the density and create product-specific conversions.
    """
    umap = _get_unit_map()
    if not umap:
        return

    existing_conversions = _grocy_get("objects/quantity_unit_conversions")
    id_to_abbrev: dict[int, str] = {v: k for k, v in umap.items()}

    # Build per-product conversion unit sets
    product_conv_units: dict[int, set[str]] = {}
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is None or cpid == "":
            continue
        pid = int(cpid)
        for qu_field in ("from_qu_id", "to_qu_id"):
            abbrev = id_to_abbrev.get(int(c[qu_field]))
            if abbrev:
                product_conv_units.setdefault(pid, set()).add(abbrev)

    need_density: list[dict] = []
    seen_pids: set[int] = set()

    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        recipe_unit = _canonical_abbrev(ing.get("unit"))
        if pid is None or recipe_unit is None or recipe_unit == "kpl":
            continue
        pid = int(pid)
        if pid in seen_pids:
            continue

        # Determine recipe domain
        if recipe_unit in _WEIGHT_UNITS:
            recipe_domain = "weight"
        elif recipe_unit in _VOLUME_UNITS:
            recipe_domain = "volume"
        else:
            continue

        # Check product's existing conversion domains
        prod_units = product_conv_units.get(pid, set())
        has_weight = bool(prod_units & _WEIGHT_UNITS)
        has_volume = bool(prod_units & _VOLUME_UNITS)

        # Already has cross-domain conversions — nothing to do
        if has_weight and has_volume:
            continue

        # Recipe needs a domain the product doesn't have
        if recipe_domain == "weight" and not has_weight and has_volume:
            pass  # product has volume, recipe wants weight → need density
        elif recipe_domain == "volume" and not has_volume and has_weight:
            pass  # product has weight, recipe wants volume → need density
        elif not has_weight and not has_volume:
            # No product-specific conversions at all — check stock unit domain
            prod = products_by_id.get(pid, {})
            stock_abbrev = id_to_abbrev.get(prod.get("qu_id_stock"))
            if stock_abbrev in _WEIGHT_UNITS and recipe_domain == "volume":
                pass  # stock is weight, recipe is volume
            elif stock_abbrev in _VOLUME_UNITS and recipe_domain == "weight":
                pass  # stock is volume, recipe is weight
            else:
                continue
        else:
            continue

        prod = products_by_id.get(pid, {})
        existing_domain = "weight" if (has_weight or id_to_abbrev.get(prod.get("qu_id_stock")) in _WEIGHT_UNITS) else "volume"
        need_density.append({
            "product_id": pid,
            "name": prod.get("name", ""),
            "has_domain": existing_domain,
        })
        seen_pids.add(pid)

    if not need_density:
        return

    product_list = json.dumps(need_density, ensure_ascii=False)

    prompt = f"""For each Finnish grocery product below, estimate the density conversion
between weight and volume units. Products already have a size in one domain
(weight or volume) — provide the conversion to the OTHER domain.

Products:
{product_list}

Return a JSON array:
[{{"product_id": <id>, "from_unit": "kg"|"g"|"l"|"dl"|"ml", "to_unit": "kg"|"g"|"l"|"dl"|"ml", "factor": <number>}}]

RULES:
- For products with weight, provide a volume equivalent (e.g. 1 kg flour → factor: 1.67, from_unit: "kg", to_unit: "l")
- For products with volume, provide a weight equivalent (e.g. 1 l milk → factor: 1.03, from_unit: "l", to_unit: "kg")
- Use common grocery densities:
  - Milk/cream/juice: ~1.03 kg/l
  - Flour (vehnäjauho): ~0.6 kg/l (1 kg ≈ 1.67 l)
  - Sugar (sokeri): ~0.85 kg/l
  - Rice (riisi): ~0.85 kg/l
  - Oil (öljy): ~0.92 kg/l
  - Butter (voi): ~0.91 kg/l
  - Honey (hunaja): ~1.4 kg/l
  - Salt (suola): ~1.2 kg/l
- If you cannot reasonably estimate the density, return null for factor
- Use the SIMPLEST conversion (prefer kg↔l over g↔ml)"""

    result = _call_gemini_json(prompt)
    if not result or not isinstance(result, list):
        log.warning("Gemini failed to estimate density conversions")
        return

    created = 0
    for item in result:
        pid = item.get("product_id")
        factor = item.get("factor")
        from_unit = item.get("from_unit")
        to_unit = item.get("to_unit")
        if pid is None or factor is None or from_unit is None or to_unit is None:
            continue

        from_id = umap.get(from_unit)
        to_id = umap.get(to_unit)
        if from_id is None or to_id is None:
            continue

        try:
            _grocy_post("objects/quantity_unit_conversions", {
                "from_qu_id": from_id,
                "to_qu_id": to_id,
                "factor": float(factor),
                "product_id": int(pid),
            })
            log.info(
                "Created density conversion for product %d (%s): 1 %s = %s %s",
                pid, products_by_id.get(pid, {}).get("name", ""), from_unit, factor, to_unit,
            )
            created += 1
        except Exception as exc:
            log.warning("Failed to create density conversion for product %d: %s", pid, exc)
            continue

        # Create derived cross-domain conversions
        derived = _derive_density_conversions(from_unit, to_unit, float(factor))
        for d_from, d_to, d_factor in derived:
            d_from_id = umap.get(d_from)
            d_to_id = umap.get(d_to)
            if d_from_id is None or d_to_id is None:
                continue
            try:
                _grocy_post("objects/quantity_unit_conversions", {
                    "from_qu_id": d_from_id,
                    "to_qu_id": d_to_id,
                    "factor": d_factor,
                    "product_id": int(pid),
                })
                created += 1
            except Exception:
                pass  # likely already exists

    if created:
        log.info("Density conversions: %d conversion(s) created.", created)


def _convert_recipe_to_stock(
    recipe_amount: float,
    recipe_qu_id: int,
    product_id: int,
    stock_qu_id: int,
    conversions: list[dict],
) -> float | None:
    """Convert a recipe amount to stock units using Grocy conversions.

    Returns the equivalent amount in stock units, or None if no conversion path exists.
    """
    if recipe_qu_id == stock_qu_id:
        return recipe_amount

    # Build a conversion graph for this product + global conversions
    conv_graph: dict[int, dict[int, float]] = {}
    for c in conversions:
        cpid = c.get("product_id")
        if cpid is not None and cpid != "" and int(cpid) != product_id:
            continue
        from_id = int(c["from_qu_id"])
        to_id = int(c["to_qu_id"])
        factor = float(c["factor"])
        conv_graph.setdefault(from_id, {})[to_id] = factor
        if factor != 0:
            conv_graph.setdefault(to_id, {})[from_id] = 1.0 / factor

    # BFS to find conversion path from recipe_qu_id to stock_qu_id
    visited = {recipe_qu_id}
    queue = [(recipe_qu_id, recipe_amount)]
    while queue:
        current_qu, current_amount = queue.pop(0)
        if current_qu == stock_qu_id:
            return current_amount
        for next_qu, factor in conv_graph.get(current_qu, {}).items():
            if next_qu not in visited:
                visited.add(next_qu)
                queue.append((next_qu, current_amount * factor))

    return None


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
    log.debug("Scraper search: '%s' → Finnish search term: '%s'", product_name, search_term)

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
            log.debug("Scraper created product for '%s'", product_name)
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
    recipe_id = int(recipe_id)

    log.info("Created recipe '%s' (ID %d)", recipe_data["name"], recipe_id)

    # Upload image if available
    if recipe_data.get("image_url"):
        filename = _upload_recipe_image(recipe_id, recipe_data["image_url"])
        if filename:
            _grocy_put(f"objects/recipes/{recipe_id}", {"picture_file_name": filename})
            log.debug("Uploaded recipe image: %s", filename)

    # Create ingredient positions
    all_products = {p["id"]: p for p in _grocy_get("objects/products")}

    # Get a known-valid QU ID as fallback
    fallback_qu_id = None
    try:
        units = _grocy_get("objects/quantity_units")
        if units:
            fallback_qu_id = units[0]["id"]
    except Exception:
        pass

    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        if not pid:
            continue
        pid = int(pid)

        prod = all_products.get(pid, {})

        # Resolve recipe unit to a Grocy QU ID
        recipe_qu_id = _resolve_unit_id(ing.get("unit"))
        if recipe_qu_id is not None:
            ing_qu_id = recipe_qu_id
        else:
            # Count items or unknown units — use product's stock QU
            ing_qu_id = prod.get("qu_id_stock") or fallback_qu_id or 1

        pos_body: dict[str, Any] = {
            "recipe_id": recipe_id,
            "product_id": pid,
            "amount": ing.get("amount") or 1,
            "qu_id": ing_qu_id,
        }
        note_parts = []
        if ing.get("note"):
            note_parts.append(ing["note"])
        if ing.get("name"):
            note_parts.append(ing["name"])
        if note_parts:
            pos_body["note"] = " — ".join(note_parts)

        try:
            _grocy_post("objects/recipes_pos", pos_body)
        except Exception as exc:
            detail = ""
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    detail = f" — {exc.response.text}"
                except Exception:
                    pass
            log.warning("Failed to add ingredient %s: %s%s", ing.get("name"), exc, detail)

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

    # Get quantity units for name lookups
    all_qu = _grocy_get("objects/quantity_units")
    qu_by_id = {u["id"]: u for u in all_qu}

    # Get all conversions for stock comparison
    all_conversions = _grocy_get("objects/quantity_unit_conversions")

    ingredients = []
    for pos in positions:
        pid = pos.get("product_id")
        product = products_by_id.get(pid, {})
        product_name = product.get("name", f"Product #{pid}")
        needed = pos.get("amount", 1)
        recipe_qu_id = pos.get("qu_id")

        stock_entry = stock_by_product.get(pid)
        in_stock_pieces = 0
        amount_opened = 0
        if stock_entry:
            in_stock_pieces = stock_entry.get("amount", 0)
            amount_opened = stock_entry.get("amount_opened", 0)

        # Resolve unit display name from QU
        qu = qu_by_id.get(recipe_qu_id, {})
        unit_abbrev = qu.get("description", "") or ""
        stock_qu_id = product.get("qu_id_stock")

        # Determine status using unit conversions
        if recipe_qu_id and stock_qu_id and recipe_qu_id != stock_qu_id:
            # Convert stock (pieces) to recipe units for comparison
            stock_in_recipe_units = _convert_recipe_to_stock(
                in_stock_pieces, stock_qu_id, pid, recipe_qu_id, all_conversions
            )
            if stock_in_recipe_units is not None:
                if stock_in_recipe_units >= needed:
                    status = "yellow" if in_stock_pieces <= 1 and amount_opened >= 1 else "green"
                else:
                    status = "red"
            else:
                # No conversion path — fall back to piece comparison
                if in_stock_pieces >= 1:
                    status = "yellow" if amount_opened >= 1 else "green"
                else:
                    status = "red"
        else:
            # Same units or count items — direct comparison
            if in_stock_pieces >= needed:
                status = "yellow" if in_stock_pieces == 1 and amount_opened >= 1 else "green"
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
            "unit_abbrev": unit_abbrev,
            "note": pos.get("note", ""),
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

    Uses unit conversions to calculate purchase amounts in pieces.
    E.g. recipe needs 8 dl milk, 1 piece = 1 L = 10 dl → add 1 piece.
    """
    detail = _get_recipe_detail(recipe_id)
    added = 0

    # Load conversions and products for smart amounts
    all_conversions = _grocy_get("objects/quantity_unit_conversions")
    products_list = _grocy_get("objects/products")
    products_by_id = {p["id"]: p for p in products_list}
    all_qu = _grocy_get("objects/quantity_units")
    qu_by_id = {u["id"]: u for u in all_qu}

    # Get recipe positions for qu_id lookup
    all_positions = _grocy_get("objects/recipes_pos")
    pos_by_id = {p["id"]: p for p in all_positions}

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

        # Calculate how many pieces to buy using conversions
        purchase_amount = 1
        pos = pos_by_id.get(ing.get("id"))
        if pos:
            recipe_qu_id = pos.get("qu_id")
            recipe_amount = pos.get("amount", 1)
            prod = products_by_id.get(pid, {})
            stock_qu_id = prod.get("qu_id_stock")

            if recipe_qu_id and stock_qu_id and recipe_qu_id != stock_qu_id:
                # Convert recipe amount to stock units (pieces)
                amount_in_pieces = _convert_recipe_to_stock(
                    recipe_amount, recipe_qu_id, pid, stock_qu_id, all_conversions
                )
                if amount_in_pieces is not None and amount_in_pieces > 0:
                    purchase_amount = math.ceil(amount_in_pieces)
            elif recipe_qu_id == stock_qu_id:
                purchase_amount = math.ceil(recipe_amount)

        try:
            _grocy_post("stock/shoppinglist/add-product", {
                "product_id": pid,
                "list_id": 1,
                "product_amount": purchase_amount,
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

    # 0. Ensure standard units and conversions exist in Grocy
    _ensure_units_and_conversions()

    # 1. Scrape the recipe
    recipe_data = _scrape_recipe(url)
    log.debug(
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
            log.debug("Matched '%s' → '%s' (ID %d)", ing["name"], match["name"], match["id"])
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
                    log.debug(
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
    if still_unmatched:
        # Look up valid QU and location IDs from Grocy
        default_qu_id = None
        default_loc_id = None
        try:
            units = _grocy_get("objects/quantity_units")
            if units:
                default_qu_id = units[0]["id"]
        except Exception:
            pass
        try:
            locs = _grocy_get("objects/locations")
            if locs:
                default_loc_id = locs[0]["id"]
        except Exception:
            pass

        if default_qu_id is None or default_loc_id is None:
            log.warning(
                "Cannot create stub products — no quantity units or locations in Grocy"
            )
        else:
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
                        "location_id": default_loc_id,
                        "qu_id_purchase": default_qu_id,
                        "qu_id_stock": default_qu_id,
                        "qu_id_consume": default_qu_id,
                        "qu_id_price": default_qu_id,
                        "treat_opened_as_out_of_stock": 0,
                        "default_best_before_days": 0,
                    })
                    new_id = resp.get("created_object_id")
                    if new_id:
                        ing["_product_id"] = int(new_id)
                        log.debug(
                            "Created stub product '%s' (ID %s)", stub_name, new_id,
                        )
                except Exception as exc:
                    log.warning("Failed to create stub product '%s': %s", stub_name, exc)

    # 7. Create product-specific unit conversions via AI
    products = _get_all_products()
    products_by_id = {p["id"]: p for p in products}
    try:
        _create_product_conversions(recipe_data["ingredients"], products_by_id)
    except Exception as exc:
        log.warning("Failed to create product conversions: %s", exc)

    # 7b. Update product default units for products without conversions
    try:
        _update_product_default_units(recipe_data["ingredients"], products_by_id)
    except Exception as exc:
        log.warning("Failed to update product default units: %s", exc)

    # 7c. Create cross-domain density conversions (weight↔volume)
    # Refresh products_by_id to include any default-unit changes from 7b
    products = _get_all_products()
    products_by_id = {p["id"]: p for p in products}
    try:
        _ensure_density_conversions(recipe_data["ingredients"], products_by_id)
    except Exception as exc:
        log.warning("Failed to create density conversions: %s", exc)

    # 8. Create recipe in Grocy
    result = _create_recipe_in_grocy(recipe_data, recipe_data["ingredients"])
    return result


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
_op_lock = threading.Lock()


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    _QUIET_PATHS = frozenset(("/api/config", "api/config", "/api/recipes"))

    def log_message(self, fmt: str, *args: Any) -> None:
        msg = fmt % args if args else fmt
        path = self.path.split("?")[0].rstrip("/") if hasattr(self, "path") else ""
        if path in self._QUIET_PATHS:
            log.debug(msg)
        else:
            log.info(msg)

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
            except BrokenPipeError:
                log.info("Client disconnected before scrape response was sent")
            except Exception as exc:
                log.exception("Scrape failed")
                try:
                    return self._json({"success": False, "error": str(exc)}, 500)
                except BrokenPipeError:
                    pass
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
    log.info("Starting recipe backend on port %d (debug=%s)", PORT, _DEBUG)
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
