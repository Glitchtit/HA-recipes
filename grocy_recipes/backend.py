"""HA Grocy Recipes — Python backend server.

Handles recipe scraping via Gemini AI, product matching against Storage,
missing-product discovery via the scraper addon, and recipe CRUD.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse, urljoin

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
STORAGE_URL = os.environ.get("STORAGE_URL", "").rstrip("/")

AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "gemini").strip().lower()
GEMINI_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash") or "gemini-2.0-flash"
OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "llama3") or "llama3"
CLAUDE_API_KEY: str = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-3-5-haiku-20241022") or "claude-3-5-haiku-20241022"

PORT = 8100

# ---------------------------------------------------------------------------
# Fetch AI config from Storage (centralised key management)
# ---------------------------------------------------------------------------
def wait_for_storage(base_url: str, max_retries: int = 30, delay: float = 5.0) -> None:
    """Block until Storage addon is reachable."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(f"{base_url}/api/health", timeout=5)
            if resp.ok:
                log.info("Storage addon is ready (%s).", resp.json().get("version", "?"))
                return
        except requests.RequestException:
            pass
        if attempt < max_retries:
            log.info("Storage not ready (attempt %d/%d), retrying in %.0fs…", attempt, max_retries, delay)
            time.sleep(delay)
    raise SystemExit("ERROR: Storage addon not reachable after %d attempts." % max_retries)


# ---------------------------------------------------------------------------
# AI client (Gemini, Ollama, Claude)
# ---------------------------------------------------------------------------
_gemini_client: genai.Client | None = None

_GEMINI_MAX_RETRIES = 4


def _extract_json_text(text: str) -> str:
    """Extract the JSON portion from an AI response that may include prose or markdown fences."""
    # Extract from code fence if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        return fence.group(1)
    # Find the first JSON object or array
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        return match.group(1)
    return text.strip()


def _get_gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(
            api_key=GEMINI_KEY,
            http_options={"timeout": 300_000},
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
            # Log token usage when available
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                log.info(
                    "Gemini usage — prompt tokens: %s, output tokens: %s, total: %s",
                    getattr(usage, "prompt_token_count", "?"),
                    getattr(usage, "candidates_token_count", "?"),
                    getattr(usage, "total_token_count", "?"),
                )
            text = resp.text or ""
            # Strip control chars that sometimes appear
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            return json.loads(text)
        except Exception as exc:
            exc_str = str(exc)
            log.warning("Gemini attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                # Server-side deadline expiry: wait longer before retry
                if "DEADLINE_EXCEEDED" in exc_str or "504" in exc_str:
                    time.sleep(30)
                else:
                    time.sleep(2 ** attempt)
    return None


def _call_ollama_json(prompt: str) -> dict | list | None:
    """Call Ollama's chat endpoint and parse the response as JSON with retries."""
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "format": "json",
                    "stream": False,
                },
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
            # Log token/timing usage
            prompt_tokens = data.get("prompt_eval_count", "?")
            output_tokens = data.get("eval_count", "?")
            total_ns = data.get("total_duration")
            total_ms = round(total_ns / 1_000_000) if total_ns else "?"
            log.info(
                "Ollama usage — prompt tokens: %s, output tokens: %s, total duration: %sms",
                prompt_tokens, output_tokens, total_ms,
            )
            content = data["message"]["content"]
            content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)
            return json.loads(content)
        except Exception as exc:
            log.warning("Ollama attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def _call_claude_json(prompt: str) -> dict | list | None:
    """Call Claude API and parse the response as JSON with retries."""
    try:
        import anthropic as _anthropic
    except ImportError:
        log.error("anthropic package not installed; cannot call Claude")
        return None
    _MAX_RETRIES = _GEMINI_MAX_RETRIES
    client = _anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            usage = response.usage
            log.info(
                "Claude usage — input tokens: %s, output tokens: %s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
            )
            text = response.content[0].text or ""
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            text = _extract_json_text(text)
            return json.loads(text)
        except Exception as exc:
            log.warning("Claude attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def _call_ai_json(prompt: str) -> dict | list | None:
    """Route AI call to Gemini, Ollama, or Claude based on configured provider."""
    if AI_PROVIDER == "ollama":
        return _call_ollama_json(prompt)
    if AI_PROVIDER == "claude":
        return _call_claude_json(prompt)
    return _call_gemini_json(prompt)


def _call_gemini_text(prompt: str) -> str | None:
    """Call Gemini for a plain-text response (no JSON mode)."""
    client = _get_gemini()
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(temperature=0.2),
            )
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                log.info(
                    "Gemini text usage — prompt tokens: %s, output tokens: %s, total: %s",
                    getattr(usage, "prompt_token_count", "?"),
                    getattr(usage, "candidates_token_count", "?"),
                    getattr(usage, "total_token_count", "?"),
                )
            return resp.text or ""
        except Exception as exc:
            exc_str = str(exc)
            log.warning("Gemini text attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                if "DEADLINE_EXCEEDED" in exc_str or "504" in exc_str:
                    time.sleep(30)
                else:
                    time.sleep(2 ** attempt)
    return None


def _call_claude_text(prompt: str) -> str | None:
    """Call Claude API for a plain-text response."""
    try:
        import anthropic as _anthropic
    except ImportError:
        log.error("anthropic package not installed; cannot call Claude")
        return None
    client = _anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text or ""
        except Exception as exc:
            log.warning("Claude text attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def _call_ollama_text(prompt: str) -> str | None:
    """Call Ollama for a plain-text response."""
    for attempt in range(1, _GEMINI_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=300,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except Exception as exc:
            log.warning("Ollama text attempt %d/%d failed: %s", attempt, _GEMINI_MAX_RETRIES, exc)
            if attempt < _GEMINI_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def _call_ai_text(prompt: str) -> str | None:
    """Route AI plain-text call to Gemini, Ollama, or Claude."""
    if AI_PROVIDER == "ollama":
        return _call_ollama_text(prompt)
    if AI_PROVIDER == "claude":
        return _call_claude_text(prompt)
    return _call_gemini_text(prompt)


# ---------------------------------------------------------------------------
# Storage API helpers
# ---------------------------------------------------------------------------
_storage_session: requests.Session | None = None


def _storage() -> requests.Session:
    global _storage_session
    if _storage_session is None:
        _storage_session = requests.Session()
    return _storage_session


def _api_get(path: str, **kwargs) -> Any:
    r = _storage().get(f"{STORAGE_URL}/api/{path}", **kwargs)
    r.raise_for_status()
    return r.json()


def _api_post(path: str, data: dict | None = None, **kwargs) -> Any:
    r = _storage().post(f"{STORAGE_URL}/api/{path}", json=data, **kwargs)
    r.raise_for_status()
    return r.json() if r.content else {}


def _api_put(path: str, data: dict | None = None, **kwargs) -> Any:
    r = _storage().put(f"{STORAGE_URL}/api/{path}", json=data, **kwargs)
    r.raise_for_status()
    return r.json() if r.content else {}


def _api_put_raw(path: str, data: bytes, content_type: str = "application/octet-stream") -> Any:
    r = _storage().put(
        f"{STORAGE_URL}/api/{path}",
        data=data,
        headers={"Content-Type": content_type},
    )
    r.raise_for_status()
    return r.json() if r.content else {}


def _api_delete(path: str) -> None:
    r = _storage().delete(f"{STORAGE_URL}/api/{path}")
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Quantity unit management
# ---------------------------------------------------------------------------

# Standard recipe units with Finnish names
_STANDARD_UNITS = [
    {"name": "Gramma", "name_plural": "Grammaa", "abbreviation": "g"},
    {"name": "Kilogramma", "name_plural": "Kilogrammaa", "abbreviation": "kg"},
    {"name": "Millilitra", "name_plural": "Millilitraa", "abbreviation": "ml"},
    {"name": "Desilitra", "name_plural": "Desilitraa", "abbreviation": "dl"},
    {"name": "Litra", "name_plural": "Litraa", "abbreviation": "l"},
    {"name": "Teelusikka", "name_plural": "Teelusikkaa", "abbreviation": "tl"},
    {"name": "Ruokalusikka", "name_plural": "Ruokalusikkaa", "abbreviation": "rkl"},
    {"name": "Ripaus", "name_plural": "Ripausta", "abbreviation": "rs"},
    {"name": "Kappale", "name_plural": "Kappaletta", "abbreviation": "kpl"},
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

# Cache: abbreviation → unit ID
_unit_map: dict[str, int] | None = None
_unit_map_lock = threading.Lock()


def _ensure_units_and_conversions() -> dict[str, int]:
    """Ensure standard recipe units and global conversions exist in Storage.

    Returns a mapping of unit abbreviation → unit ID.
    Idempotent — skips units/conversions that already exist.
    """
    global _unit_map
    with _unit_map_lock:
        if _unit_map is not None:
            return _unit_map

        existing_units = _api_get("units")
        existing_by_abbrev = {}
        existing_by_name = {}
        for u in existing_units:
            if u.get("abbreviation"):
                existing_by_abbrev[u["abbreviation"].lower().strip()] = u["id"]
            if u.get("name"):
                existing_by_name[u["name"].lower().strip()] = u["id"]

        abbrev_to_id: dict[str, int] = {}

        for unit_def in _STANDARD_UNITS:
            abbrev = unit_def["abbreviation"]
            # Check if unit already exists (by abbreviation or name)
            uid = existing_by_abbrev.get(abbrev.lower())
            if uid is None:
                uid = existing_by_name.get(unit_def["name"].lower())
            if uid is None:
                try:
                    resp = _api_post("units", {
                        "name": unit_def["name"],
                        "abbreviation": abbrev,
                        "name_plural": unit_def["name_plural"],
                    })
                    uid = int(resp.get("id", 0))
                    log.debug("Created unit '%s' (ID %d)", unit_def["name"], uid)
                except Exception as exc:
                    log.warning("Failed to create unit '%s': %s", unit_def["name"], exc)
                    continue
            abbrev_to_id[abbrev] = uid

        # Also map the "Piece"/"Pack" defaults if they exist
        for u in existing_units:
            name_lower = (u.get("name") or "").lower().strip()
            if name_lower in ("piece", "pack", "stück", "kappale"):
                abbrev_to_id.setdefault("piece", u["id"])
            abbrev_lower = (u.get("abbreviation") or "").lower().strip()
            if abbrev_lower == "kpl":
                abbrev_to_id.setdefault("piece", u["id"])

        # Create global conversions
        existing_conversions = _api_get("conversions")
        conv_set = set()
        for c in existing_conversions:
            if c.get("product_id") is None:
                conv_set.add((int(c["from_unit_id"]), int(c["to_unit_id"])))

        for from_abbrev, to_abbrev, factor in _GLOBAL_CONVERSIONS:
            from_id = abbrev_to_id.get(from_abbrev)
            to_id = abbrev_to_id.get(to_abbrev)
            if from_id is None or to_id is None:
                continue
            if (from_id, to_id) in conv_set:
                continue
            try:
                _api_post("conversions", {
                    "from_unit_id": from_id,
                    "to_unit_id": to_id,
                    "factor": factor,
                })
                log.debug("Created global conversion: 1 %s = %s %s", from_abbrev, factor, to_abbrev)
            except Exception as exc:
                log.warning("Failed to create conversion %s→%s: %s", from_abbrev, to_abbrev, exc)

        _unit_map = abbrev_to_id
        log.debug("Unit map initialised: %s", {k: v for k, v in abbrev_to_id.items()})
        return _unit_map


def _get_unit_map() -> dict[str, int]:
    """Get the cached unit abbreviation → ID mapping, initialising if needed."""
    if _unit_map is not None:
        return _unit_map
    return _ensure_units_and_conversions()


def _resolve_unit_id(unit_str: str | None) -> int | None:
    """Resolve a unit string (e.g. 'dl', 'gram', 'kpl') to a unit ID.

    Returns None only when the unit string is empty or unrecognised.
    """
    if not unit_str:
        return None
    canonical = _UNIT_ALIASES.get(unit_str.lower().strip())
    if canonical is None:
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
    matched_ingredients: list[dict], products_by_id: dict[int, dict],
    *, skip_product_ids: set[int] | None = None,
) -> None:
    """Use Gemini AI to determine product package sizes and create conversions.

    For each matched product, analyse the product name to determine the package
    size (e.g. "Arla Kevytmaito 1L" → 1 piece = 1 litre) and create a
    product-specific unit conversion in Storage.

    Products in *skip_product_ids* (e.g. stubs with no package info) are skipped.
    """
    umap = _get_unit_map()
    if not umap:
        return

    _skip = skip_product_ids or set()

    # Collect products that need conversions
    products_to_check = []
    for ing in matched_ingredients:
        pid = ing.get("_product_id")
        recipe_unit = _canonical_abbrev(ing.get("unit"))
        if pid is None or recipe_unit is None or recipe_unit == "kpl":
            continue
        pid = int(pid)
        if pid in _skip:
            continue
        prod = products_by_id.get(pid, {})
        products_to_check.append({
            "product_id": pid,
            "product_name": prod.get("name", ""),
            "recipe_unit": recipe_unit,
        })

    if not products_to_check:
        return

    # Check which products already have conversions
    existing_conversions = _api_get("conversions")
    products_with_conv: set[int] = set()
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is not None:
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

    result = _call_ai_json(prompt)
    if not result or not isinstance(result, list):
        log.warning("Gemini failed to determine product package sizes")
        return

    piece_id = umap.get("piece") or umap.get("kpl")
    if piece_id is None:
        # Try to find a "Piece" unit from existing units
        all_units = _api_get("units")
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

        to_unit_id = umap.get(unit_abbrev)
        if to_unit_id is None:
            continue

        try:
            _api_post("conversions", {
                "from_unit_id": piece_id,
                "to_unit_id": to_unit_id,
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
    """Update product default unit to match recipe unit when no conversion exists.

    For products where the recipe uses a measurable unit (g, dl, etc.) but the
    product's unit_id is still the generic Piece and no product-specific
    conversion was created by AI, change the product's default unit to the
    recipe unit.  E.g. "Turskafile" unit → grams.
    """
    umap = _get_unit_map()
    if not umap:
        return

    existing_conversions = _api_get("conversions")
    products_with_conv: set[int] = set()
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is not None:
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

        recipe_unit_id = umap.get(recipe_unit)
        if recipe_unit_id is None:
            continue

        prod = products_by_id.get(pid, {})
        current_unit_id = prod.get("unit_id")
        if current_unit_id == recipe_unit_id:
            continue

        try:
            _api_put(f"products/{pid}", {
                "unit_id": recipe_unit_id,
            })
            updated.add(pid)
            log.debug(
                "Updated product %d (%s) default unit to %s",
                pid, prod.get("name", ""), recipe_unit,
            )
        except Exception as exc:
            log.warning("Failed to update product %d default unit: %s", pid, exc)


def _ensure_density_conversions(
    matched_ingredients: list[dict], products_by_id: dict[int, dict],
    *, skip_product_ids: set[int] | None = None,
) -> None:
    """Create cross-domain (weight↔volume) density conversions for products.

    For each ingredient whose recipe unit is in a different domain (weight vs
    volume) than the product's existing conversions, use Gemini AI to estimate
    the density and create product-specific conversions.

    Products in *skip_product_ids* (e.g. stubs with no real product info) are
    skipped — Gemini cannot estimate density for generic names.
    """
    umap = _get_unit_map()
    if not umap:
        return

    _skip = skip_product_ids or set()

    existing_conversions = _api_get("conversions")
    id_to_abbrev: dict[int, str] = {v: k for k, v in umap.items()}

    # Build per-product conversion unit sets
    product_conv_units: dict[int, set[str]] = {}
    for c in existing_conversions:
        cpid = c.get("product_id")
        if cpid is None:
            continue
        pid = int(cpid)
        for field in ("from_unit_id", "to_unit_id"):
            abbrev = id_to_abbrev.get(int(c[field]))
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
        if pid in _skip:
            continue
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

        # Already has cross-domain conversions — skip
        if has_weight and has_volume:
            seen_pids.add(pid)
            continue

        # Recipe needs a domain the product doesn't have
        if recipe_domain == "weight" and not has_weight and has_volume:
            pass
        elif recipe_domain == "volume" and not has_volume and has_weight:
            pass
        elif not has_weight and not has_volume:
            prod = products_by_id.get(pid, {})
            stock_abbrev = id_to_abbrev.get(prod.get("unit_id"))
            if stock_abbrev in _WEIGHT_UNITS and recipe_domain == "volume":
                pass
            elif stock_abbrev in _VOLUME_UNITS and recipe_domain == "weight":
                pass
            else:
                continue
        else:
            continue

        prod = products_by_id.get(pid, {})
        existing_domain = "weight" if (has_weight or id_to_abbrev.get(prod.get("unit_id")) in _WEIGHT_UNITS) else "volume"
        need_density.append({
            "product_id": pid,
            "name": prod.get("name", ""),
            "has_domain": existing_domain,
        })
        seen_pids.add(pid)

    created = 0
    if not need_density:
        log.debug("No products need new density conversions from Gemini.")
    else:
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

        result = _call_ai_json(prompt)
        if not result or not isinstance(result, list):
            log.warning("Gemini failed to estimate density conversions")
        else:
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
                    _api_post("conversions", {
                        "from_unit_id": from_id,
                        "to_unit_id": to_id,
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
                        _api_post("conversions", {
                            "from_unit_id": d_from_id,
                            "to_unit_id": d_to_id,
                            "factor": d_factor,
                            "product_id": int(pid),
                        })
                        created += 1
                    except Exception:
                        pass  # likely already exists

    # Propagate density conversions from parent products to children.
    if seen_pids:
        all_convs = _api_get("conversions")
        children_of: dict[int, list[int]] = {}
        for p in products_by_id.values():
            ppid = p.get("parent_id")
            if ppid:
                children_of.setdefault(int(ppid), []).append(p["id"])

        for pid in seen_pids:
            child_ids = children_of.get(pid, [])
            if not child_ids:
                continue
            parent_density = [
                c for c in all_convs
                if c.get("product_id") is not None
                and int(c["product_id"]) == pid
                and id_to_abbrev.get(int(c["from_unit_id"])) in (_WEIGHT_UNITS | _VOLUME_UNITS)
                and id_to_abbrev.get(int(c["to_unit_id"])) in (_WEIGHT_UNITS | _VOLUME_UNITS)
            ]
            if not parent_density:
                continue
            for cid in child_ids:
                child_existing = {
                    (int(c["from_unit_id"]), int(c["to_unit_id"]))
                    for c in all_convs
                    if c.get("product_id") is not None
                    and int(c["product_id"]) == cid
                }
                propagated = 0
                for pc in parent_density:
                    pair = (int(pc["from_unit_id"]), int(pc["to_unit_id"]))
                    if pair in child_existing:
                        continue
                    try:
                        _api_post("conversions", {
                            "from_unit_id": pair[0],
                            "to_unit_id": pair[1],
                            "factor": float(pc["factor"]),
                            "product_id": cid,
                        })
                        created += 1
                        propagated += 1
                    except Exception:
                        pass  # likely already exists
                if propagated:
                    child_name = products_by_id.get(cid, {}).get("name", str(cid))
                    log.info("Propagated %d density conversion(s) to child product %d (%s).",
                             propagated, cid, child_name)

    if created:
        log.info("Density conversions: %d conversion(s) created total.", created)


def _convert_recipe_to_stock(
    recipe_amount: float,
    recipe_unit_id: int,
    product_id: int,
    stock_unit_id: int,
    conversions: list[dict],
) -> float | None:
    """Convert a recipe amount to stock units using conversions.

    Returns the equivalent amount in stock units, or None if no conversion path exists.
    """
    if recipe_unit_id == stock_unit_id:
        return recipe_amount

    # Build a conversion graph for this product + global conversions
    conv_graph: dict[int, dict[int, float]] = {}
    for c in conversions:
        cpid = c.get("product_id")
        if cpid is not None and int(cpid) != product_id:
            continue
        from_id = int(c["from_unit_id"])
        to_id = int(c["to_unit_id"])
        factor = float(c["factor"])
        conv_graph.setdefault(from_id, {})[to_id] = factor
        if factor != 0:
            conv_graph.setdefault(to_id, {})[from_id] = 1.0 / factor

    # BFS to find conversion path from recipe_unit_id to stock_unit_id
    visited = {recipe_unit_id}
    queue = [(recipe_unit_id, recipe_amount)]
    while queue:
        current_unit, current_amount = queue.pop(0)
        if current_unit == stock_unit_id:
            return current_amount
        for next_unit, factor in conv_graph.get(current_unit, {}).items():
            if next_unit not in visited:
                visited.add(next_unit)
                queue.append((next_unit, current_amount * factor))

    return None


# ---------------------------------------------------------------------------
# Scraper proxy
# ---------------------------------------------------------------------------
def _scraper_available() -> bool:
    """Check if the scraper addon is reachable (via nginx proxy)."""
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

    result = _call_ai_json(prompt)
    if result and isinstance(result, dict) and result.get("search_term"):
        return result["search_term"]
    return ingredient_name


def _batch_translate_to_finnish(names: list[str]) -> dict[str, str]:
    """Translate multiple ingredient names to Finnish search terms in one Gemini call.

    Returns a mapping of original name → Finnish search term.
    Falls back to the original name if translation fails.
    """
    if not names:
        return {}
    if len(names) == 1:
        return {names[0]: _translate_to_finnish_search(names[0])}

    names_json = json.dumps(
        [{"index": i, "name": n} for i, n in enumerate(names)],
        ensure_ascii=False,
    )

    prompt = f"""Translate these ingredient names to short Finnish grocery search terms.

Ingredients:
{names_json}

Return a JSON array: [{{"index": 0, "search_term": "finnish term"}}]

Rules:
- Return a simple Finnish word suitable for searching a Finnish grocery store website (k-ruoka.fi).
- Use common Finnish grocery terms, e.g.: "torskfilé" → "turska", "butter" → "voi", "cream" → "kerma", "chicken breast" → "kananrinta", "bread crumbs" → "korppujauho"
- Keep it short — 1-2 words maximum per ingredient. Just the product type, no brands or quantities.
- If already in Finnish, return as-is."""

    result = _call_ai_json(prompt)
    mapping: dict[str, str] = {}
    if result and isinstance(result, list):
        for item in result:
            idx = item.get("index")
            term = item.get("search_term")
            if idx is not None and term and 0 <= idx < len(names):
                mapping[names[idx]] = term

    # Fill in any missing translations with original names
    for n in names:
        if n not in mapping:
            mapping[n] = n
    return mapping


def _scraper_discover(product_name: str, search_term: str | None = None) -> dict | None:
    """Ask the scraper addon to find and create a product by name search.

    If *search_term* is provided it is used directly; otherwise the ingredient
    name is translated to Finnish via a Gemini call first.
    """
    if search_term is None:
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
        "User-Agent": "Mozilla/5.0 (compatible; RecipeBackend/1.0)"
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
                "User-Agent": "Mozilla/5.0 (compatible; RecipeBackend/1.0)"
            })
            html = r.text
        soup = BeautifulSoup(html, "html.parser")
        # Try og:image first
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return urljoin(url, og["content"])
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
                    if img:
                        return urljoin(url, img)
                    return None
            except Exception:
                continue
        return None
    except Exception:
        return None


def _find_jsonld_recipe(html: str) -> dict | None:
    """Find and return a schema.org Recipe object from JSON-LD script tags."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            # Handle @graph wrapper
            if "@graph" in item:
                for node in item["@graph"]:
                    if isinstance(node, dict) and node.get("@type") == "Recipe":
                        return node
            if item.get("@type") == "Recipe":
                return item
    return None


def _parse_jsonld_servings(yield_val: Any) -> int:
    """Parse recipeYield into an integer serving count."""
    if isinstance(yield_val, (int, float)):
        return int(yield_val)
    if isinstance(yield_val, list) and yield_val:
        yield_val = yield_val[0]
    if isinstance(yield_val, str):
        m = re.search(r"\d+", yield_val)
        if m:
            return int(m.group())
    return 4


def _translate_ingredients(raw_ingredients: list[str]) -> list[dict]:
    """Use Gemini to parse and translate a list of ingredient strings to Finnish.

    Input: ["2 dl mjölk", "1 msk smör", "salt"]
    Output: [{"name": "maito", "amount": 2.0, "unit": "dl", "note": null}, ...]
    """
    if not raw_ingredients:
        return []

    lines = "\n".join(f"- {ing}" for ing in raw_ingredients)
    prompt = f"""Parse and translate these recipe ingredients to Finnish structured JSON.

Ingredient strings:
{lines}

Return a JSON array, one object per ingredient:
[{{"name": "Finnish ingredient name", "amount": <number or null>, "unit": "unit or null", "note": "prep note or null"}}]

RULES:
- Translate ALL ingredient names to Finnish (smör→voi, mjölk→maito, butter→voi, milk→maito, salt→suola, flour→vehnäjauho, egg→kananmuna, ägg→kananmuna, potato→peruna, lök→sipuli, vitlök→valkosipuli, etc.)
- Name must be a simple generic product name (e.g. "kananmuna" not "3 kananmunaa")
- Amount: extract the numeric quantity (float), or null if absent
- PARENTHETICAL ANNOTATIONS: If a string contains a weight/calorie note in parentheses like "(ca 120 g)", "(about 180g)", "(500 kcal)" → IGNORE the parenthetical. Use only the primary amount/unit before it.
  Example: "2 ägg (ca 120 g)" → amount=2, unit="kpl" NOT amount=120, unit="g"
- "TO TASTE" ingredients: If the source says "to taste", "salta väl", "maun mukaan", "efter smak", "salt and pepper", "season with..." or similar → amount=null, unit=null
- Unit rules (CRITICAL — follow exactly):
  * If the source already has a unit (dl, ml, l, g, kg, tsk, msk, tbsp, tsp, cup, etc.) → translate it to the Finnish abbreviation (dl, ml, l, g, kg, tl, rkl)
  * If the source has NO unit and the ingredient is a whole countable item → unit = "kpl"
    Countable items: egg/ägg/kananmuna, onion/lök/sipuli, potato/potatis/peruna, carrot/morot/porkkana, lemon/citron/sitruuna, lime, orange/appelsin/appelsiini, banana/banan/banaani, avocado/avokado, apple/äpple/omena, bell pepper/paprika, tomato/tomat/tomaatti, cucumber/gurka/kurkku, zucchini/kesäkurpitsa, eggplant/aubergine/munakoisio, chicken breast/kananrinta, chicken thigh/kananreisi, steak/pihvi, sausage/makkara, tortilla, bread roll/sämpylä, garlic clove/vitlöksklyfta/valkosipulinkynsi, chili
  * If the source has NO unit and the ingredient is not a countable item (salt, pepper, oil, etc.) → unit = null
  * NEVER invent a weight unit (g/kg) when no unit is given in the source string
- Note: preparation detail like "hienonnettu", "viipaloitu", or null
- Examples:
  "2 ägg" → name="kananmuna", amount=2, unit="kpl"
  "2 ägg (ca 120 g)" → name="kananmuna", amount=2, unit="kpl"
  "3 eggs" → name="kananmuna", amount=3, unit="kpl"
  "1 lök" → name="sipuli", amount=1, unit="kpl"
  "1 chicken breast" → name="kananrinta", amount=1, unit="kpl"
  "4 potatisar" → name="peruna", amount=4, unit="kpl"
  "1 zucchini" → name="kesäkurpitsa", amount=1, unit="kpl"
  "500 g kyckling" → name="kana", amount=500, unit="g"
  "salt" → name="suola", amount=null, unit=null
  "salta väl" → name="suola", amount=null, unit=null
  "peppar efter smak" → name="pippuri", amount=null, unit=null
- Do NOT include any text outside the JSON array"""

    result = _call_ai_json(prompt)
    if not result or not isinstance(result, list):
        log.warning("Ingredient translation failed — returning empty list")
        return []
    return _fix_countable_units(result)


# Known countable ingredient keywords (Finnish) — never use g/kg for these
_COUNTABLE_KEYWORDS = {
    # Eggs
    "kananmuna", "muna",
    # Onions
    "sipuli", "punasipuli", "kevätsipuli", "salottisipuli",
    # Root vegetables
    "peruna", "porkkana", "lanttu", "palsternakka", "nauris", "punajuuri", "bataatti",
    # Other vegetables
    "tomaatti", "paprika", "kurkku", "selleri", "kesäkurpitsa", "munakoisio",
    "chili", "jalapeño", "artisokka",
    # Fruits & citrus
    "sitruuna", "lime", "appelsiini", "banaani", "avokado", "omena", "päärynä",
    "persikka", "mango", "kiivi", "luumu", "greippi", "nektariini",
    # Garlic
    "valkosipuli", "valkosipulinkynsi",
    # Meat pieces
    "kanankoipi", "kananreisi", "kananrinta", "pihvi", "kyljys", "makkara",
    # Bread & wraps
    "sämpylä", "leipä", "tortilla", "pita", "rieskä",
    # Other countable
    "kääretorttu", "munkkipossu",
}


def _fix_countable_units(ingredients: list[dict]) -> list[dict]:
    """Post-processor: force unit=kpl for countable items when AI returns g/kg with small count.

    Prevents "2 kananmuna" being stored as "2 g kananmuna" which is nonsensical.
    Only triggers when amount <= 50 (larger values are plausible weights).
    """
    for ing in ingredients:
        if not isinstance(ing, dict):
            continue
        name = (ing.get("name") or "").lower()
        unit = (ing.get("unit") or "").lower()
        amount = ing.get("amount")
        if unit in ("g", "kg") and amount is not None and float(amount) <= 50:
            if any(kw in name for kw in _COUNTABLE_KEYWORDS):
                log.info("_fix_countable_units: %s %s %s → kpl", amount, unit, name)
                ing["unit"] = "kpl"
    return ingredients


def _summarize_recipe(page_text: str, url: str) -> str | None:
    """Step 1 of two-step extraction: ask AI to summarize the recipe page as plain text.

    The summary normalizes ingredient strings:
    - Strips parenthetical weight/calorie annotations
    - Marks 'to taste' ingredients explicitly
    This produces cleaner input for the JSON extraction step.
    """
    prompt = f"""Summarize this recipe page into a clean, structured plain-text recipe.

URL: {url}

Page content:
{page_text}

Instructions:
1. Write the recipe name on the first line.
2. Write the number of servings (e.g. "Servings: 4").
3. List ingredients, one per line, in this exact format:
   <amount> <unit> <ingredient name>
   Rules for ingredients:
   - REMOVE any parenthetical weight or calorie notes: "2 ägg (ca 120 g)" → write "2 ägg"
   - "To taste" / "salta väl" / "maun mukaan" / "efter smak" → write "to taste <ingredient name>"
   - Keep amounts and units EXACTLY as written in the source — do not convert, translate, or add units
   - If an ingredient has NO unit written (just a number + item name, e.g. "2 ägg", "1 citron", "3 tomater"), write it EXACTLY as-is without adding any unit. Do NOT insert g, kg, ml, dl, or any other unit.
   - Examples: "2 ägg" → "2 ägg" (NOT "2 st ägg"), "1 citron" → "1 citron" (NOT "1 g citron")
4. List the cooking steps numbered 1, 2, 3...

Write ONLY the clean recipe. No extra commentary, no markdown formatting."""

    return _call_ai_text(prompt)


def _extract_recipe_from_summary(summary: str, url: str, image_url: str | None) -> dict:
    """Step 2 of two-step extraction: extract structured JSON from the clean summary text.

    The summary has already been normalized (parenthetical weights removed,
    to-taste items marked), so this step only needs to translate to Finnish and
    produce the final JSON.
    """
    prompt = f"""Extract a structured recipe JSON from this summary text.

Summary:
{summary}

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
- ALWAYS translate ingredient names to Finnish, regardless of the source language.
  Examples: "smör" → "voi", "butter" → "voi", "torskfilé" → "turskafile", "salt" → "suola",
  "milk" → "maito", "ägg" → "kananmuna", "flour" → "vehnäjauho", "potatis" → "peruna",
  "onion" → "sipuli", "garlic" → "valkosipuli", "carrot" → "porkkana"
- Keep ingredient names as simple generic product names (e.g. "kananmuna" not "3 kananmunaa")
- The recipe name and instructions should stay in the original language.

"TO TASTE" RULE:
- If a line in the summary starts with "to taste" (e.g. "to taste salt", "to taste pepper")
  → set amount=null and unit=null for that ingredient
  Example: "to taste salt" → {{"name": "suola", "amount": null, "unit": null}}

UNIT RULES (CRITICAL — follow these exactly):
- If the source already has a unit (dl, ml, l, g, kg, tsk, msk, tbsp, tsp, cup) → translate to Finnish abbreviation (dl, ml, l, g, kg, tl, rkl)
- If NO unit and ingredient is a whole countable item → unit = "kpl"
  Countable items include: eggs, onions, potatoes, carrots, lemons, limes, oranges, bananas, avocados, apples, pears, bell peppers, tomatoes, cucumbers, zucchini, eggplants, chicken breasts/thighs/legs, steaks, sausages, tortillas, bread rolls, garlic cloves, chili peppers
- If NO unit and ingredient is not countable (salt, pepper, oil, butter, flour, sugar, etc.) → unit = null
- NEVER invent a weight (g/kg) when no unit appears in the source line
- Examples:
  "2 ägg" → unit="kpl"  |  "3 eggs" → unit="kpl"  |  "1 lök" → unit="kpl"
  "1 chicken breast" → unit="kpl"  |  "4 potatis" → unit="kpl"
  "1 zucchini" → unit="kpl"  |  "2 tortillas" → unit="kpl"
  "500 g chicken" → unit="g" (weight given — NOT kpl)
  "2 dl cream" → unit="dl" (volume given — NOT kpl)
  "salt" → unit=null  |  "pepper to taste" → unit=null

- Do NOT include any text outside the JSON object"""

    result = _call_ai_json(prompt)
    if not result or not isinstance(result, dict):
        raise ValueError("Failed to extract recipe JSON from summary")

    if "ingredients" in result and isinstance(result["ingredients"], list):
        result["ingredients"] = _fix_countable_units(result["ingredients"])

    result["source_url"] = url
    result["image_url"] = image_url
    return result


def _scrape_recipe(url: str) -> dict:
    """Scrape a recipe from URL using a two-step AI pipeline.

    Step 1 — summarize: AI normalizes the raw page text (strips parenthetical
    weight annotations, marks 'to taste' ingredients) into a clean recipe summary.
    Step 2 — extract: AI extracts structured Finnish JSON from the clean summary.

    All recipes go through both steps regardless of whether JSON-LD is present,
    ensuring consistent ingredient parsing quality.

    Returns: {name, image_url, servings, source_url, ingredients: [{name, amount, unit, note}], instructions: [str]}
    """
    r = requests.get(url, timeout=15, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RecipeBackend/1.0)"
    })
    r.raise_for_status()
    raw_html = r.text
    image_url = _extract_image_url(url, raw_html)

    page_text = BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)[:8000]

    log.info("Summarizing recipe page for %s", url)
    summary = _summarize_recipe(page_text, url)
    if not summary:
        raise ValueError("Failed to summarize recipe page")

    return _extract_recipe_from_summary(summary, url, image_url)



# ---------------------------------------------------------------------------
# Product matching
# ---------------------------------------------------------------------------
def _get_all_products() -> list[dict]:
    """Get all products from Storage."""
    return _api_get("products")


def _get_group_master_products() -> tuple[int | None, list[dict]]:
    """Return (group_master_id, products in 'Group master' group including inactive).

    Inactive products are included so stubs created by previous scrapes are
    visible and will be matched instead of being duplicated.
    Returns (None, []) if the group doesn't exist yet.
    """
    try:
        groups = _api_get("product-groups")
        group_master_id = None
        for g in groups:
            if g.get("name") == "Group master":
                group_master_id = g["id"]
                break
        if group_master_id is None:
            return None, []
        gm_products = _api_get(
            "products",
            params={"active_only": "false", "group_id": str(group_master_id)},
        )
        return group_master_id, gm_products
    except Exception as exc:
        log.warning("Could not fetch Group master products: %s", exc)
        return None, []


def _match_ingredient(
    name: str,
    products: list[dict],
    group_masters: list[dict] | None = None,
) -> dict | None:
    """Find the best matching product for an ingredient name.

    When *group_masters* is provided (even if empty), matching is restricted
    to that list — recipes always link to Group master category products.
    When *group_masters* is None, falls back to exact-match over all products
    (climbing to parent if a child is matched).
    """
    name_lower = name.lower().strip()
    candidates = group_masters if group_masters is not None else products

    for p in candidates:
        if p["name"].lower().strip() == name_lower:
            # Group master products have no parent_id, but keep climb logic
            # when called without group_masters (legacy fallback path).
            if p.get("parent_id"):
                parent = next(
                    (pp for pp in products if pp["id"] == int(p["parent_id"])),
                    None,
                )
                if parent:
                    return parent
            return p

    return None


def _ai_match_ingredients(
    ingredients: list[dict],
    products: list[dict],
    group_masters: list[dict] | None = None,
) -> list[dict]:
    """Use Gemini AI to match ingredients to Storage products when simple matching fails."""
    unmatched = [i for i in ingredients if i.get("_product_id") is None]
    if not unmatched:
        return ingredients

    # Restrict AI to Group master products when available so recipes always
    # link to general-category products, not specific branded variants.
    if group_masters is not None:
        matchable = group_masters
    else:
        matchable = [p for p in products if not p.get("parent_id")]
        if not matchable:
            matchable = products

    if not matchable:
        return ingredients

    product_names = [{"id": p["id"], "name": p["name"]} for p in matchable]

    ingredient_list = json.dumps(
        [{"index": i, "name": ing["name"]} for i, ing in enumerate(unmatched)],
        ensure_ascii=False,
    )
    product_list = json.dumps(product_names[:500], ensure_ascii=False)

    prompt = f"""Match these recipe ingredients to the closest product.

IMPORTANT CONTEXT:
- This household speaks Swedish, Finnish, and English.
- Recipes may be in ANY of these languages.
- ALL product names are in Finnish.
- Ingredient names below have been translated to Finnish, but may still have slight variations.

Ingredients to match:
{ingredient_list}

Available products (these are general-category products, not specific brands):
{product_list}

Return a JSON array of objects:
[{{"index": 0, "product_id": <matched product ID or null if no match>, "confidence": "high"|"medium"|"low"}}]

MATCHING RULES:
- Match by ingredient TYPE and MEANING, not by brand name or substring.
  Example: "suola" (salt) should match "Suola" — NOT "Lay's Chips Salted" or any chip/crisp product.
- "voi" (butter) should match "Voi" — NOT "Voileipäkeksi" (sandwich cookie).
- A product here represents a general category (e.g. "Maito" = any milk, "Voi" = any butter).
- Only match with "high" or "medium" confidence — set product_id to null for poor or uncertain matches.
- Do NOT match based on a word appearing inside a brand name or product description.
- If the ingredient is a basic staple (suola, pippuri, sokeri, voi, maito, jauho), look for the generic product."""

    result = _call_ai_json(prompt)
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


def _deduplicate_stub_candidates(
    unmatched: list[dict],
    all_products: list[dict],
    group_masters: list[dict] | None = None,
) -> list[dict]:
    """Match unmatched ingredients to existing products via normalized/fuzzy matching.

    Runs a broader matching pass than _match_ingredient (which only does exact name match
    on Group masters).  This catches substring containment and case variations across ALL
    products, then uses AI as a final check for semantic duplicates.

    Returns *unmatched* with ``_product_id`` set where a match is found.
    """
    if not unmatched or not all_products:
        return unmatched

    product_lookup: list[tuple[str, dict]] = [
        (p["name"].lower().strip(), p) for p in all_products
    ]

    still_need_ai: list[dict] = []

    for ing in unmatched:
        if ing.get("_product_id") is not None:
            continue
        ing_name = ing["name"].lower().strip()

        matched: dict | None = None

        # 1. Exact normalized match against ALL products
        for pname, prod in product_lookup:
            if pname == ing_name:
                matched = prod
                break

        # 2. Substring containment (min 3 chars, word-boundary only to avoid false
        #    positives like "voi" matching inside "virvoitusjuoma")
        if not matched:
            for pname, prod in product_lookup:
                if len(ing_name) < 3:
                    continue
                pattern = r"(?<!\w)" + re.escape(ing_name) + r"(?!\w)"
                pname_pattern = r"(?<!\w)" + re.escape(pname) + r"(?!\w)"
                if re.search(pattern, pname) or re.search(pname_pattern, ing_name):
                    if not prod.get("parent_id"):
                        matched = prod
                        break

        # Climb to parent if a child was matched
        if matched and matched.get("parent_id"):
            parent = next(
                (p for p in all_products if p["id"] == int(matched["parent_id"])),
                None,
            )
            if parent:
                matched = parent

        if matched:
            ing["_product_id"] = matched["id"]
            log.info(
                "Dedup matched '%s' → '%s' (ID %d)",
                ing["name"], matched["name"], matched["id"],
            )
        else:
            still_need_ai.append(ing)

    # 3. AI semantic dedup for remaining candidates
    if still_need_ai and group_masters:
        parent_names = [{"id": p["id"], "name": p["name"]} for p in group_masters]
        if parent_names:
            candidate_list = json.dumps(
                [{"index": i, "name": ing["name"]} for i, ing in enumerate(still_need_ai)],
                ensure_ascii=False,
            )
            parent_list = json.dumps(parent_names[:500], ensure_ascii=False)

            prompt = f"""Check if any of these candidate ingredient names are duplicates of existing products.

Candidate ingredients (about to be created as new products):
{candidate_list}

Existing parent products in the system:
{parent_list}

For each candidate, check if an existing product already represents the SAME ingredient.
Consider: singular/plural forms, spelling variations, abbreviations, and synonyms.

Return a JSON array:
[{{"index": 0, "product_id": <matched existing product ID or null>, "reason": "brief explanation"}}]

RULES:
- Only match when confident they represent the SAME ingredient
- "sitruuna" and "Sitruuna" → MATCH (case)
- "valkosipulinkynsi" and "Valkosipuli" → MATCH (clove of garlic IS garlic)
- "kanafilee" and "Kana" → MATCH (chicken fillet IS chicken)
- Do NOT match unrelated items even if names partially overlap"""

            result = _call_ai_json(prompt)
            if result and isinstance(result, list):
                for match in result:
                    idx = match.get("index")
                    pid = match.get("product_id")
                    if idx is not None and pid is not None and 0 <= idx < len(still_need_ai):
                        still_need_ai[idx]["_product_id"] = int(pid)
                        log.info(
                            "AI dedup matched '%s' → product ID %d (%s)",
                            still_need_ai[idx]["name"], pid, match.get("reason", ""),
                        )

    return unmatched


# ---------------------------------------------------------------------------
# Recipe CRUD
# ---------------------------------------------------------------------------
def _upload_recipe_image(recipe_id: int, image_url: str, old_filename: str | None = None) -> str | None:
    """Download image from URL and upload to Storage."""
    try:
        log.debug("Downloading recipe image from: %s", image_url)
        r = requests.get(image_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; RecipeBackend/1.0)"
        })
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            log.warning("Skipping image upload: Content-Type is '%s', not an image", content_type)
            return None
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        # Use a random token to prevent filename collisions on ID reuse (e.g. after factory reset)
        unique_token = uuid.uuid4().hex[:8]
        filename = f"recipe_{recipe_id}_{unique_token}.{ext}"

        _api_put_raw(f"files/recipes/{filename}", r.content, content_type=content_type)
        log.debug("Uploaded recipe image: %s", filename)

        # Clean up the old image file if replacing
        if old_filename and old_filename != filename:
            try:
                _api_delete(f"files/recipes/{old_filename}")
                log.debug("Deleted old recipe image: %s", old_filename)
            except Exception as exc:
                log.warning("Failed to delete old recipe image '%s': %s", old_filename, exc)

        return filename
    except Exception as exc:
        log.warning("Failed to upload recipe image: %s", exc)
        return None


def _create_recipe(recipe_data: dict, matched_ingredients: list[dict]) -> dict:
    """Create a recipe in Storage with all its ingredients.

    Uses a single POST with ingredients array.
    Returns the created recipe with IDs.
    """
    # Build ingredients list
    ingredients = []
    all_products = {p["id"]: p for p in _api_get("products")}

    for idx, ing in enumerate(matched_ingredients):
        pid = ing.get("_product_id")
        if not pid:
            continue
        pid = int(pid)
        prod = all_products.get(pid, {})

        # Resolve recipe unit to a unit ID
        recipe_unit_id = _resolve_unit_id(ing.get("unit"))
        if recipe_unit_id is None:
            # No unit specified (e.g. "to taste" items) — use product's default
            recipe_unit_id = prod.get("unit_id") or 1

        note_parts = []
        if ing.get("note"):
            note_parts.append(ing["note"])
        if ing.get("name"):
            note_parts.append(ing["name"])

        ingredients.append({
            "product_id": pid,
            "amount": float(ing.get("amount") or 0),
            "unit_id": recipe_unit_id,
            "note": " — ".join(note_parts) if note_parts else "",
            "sort_order": idx,
        })

    # Build description with source URL
    description = "\n".join(recipe_data.get("instructions", []))
    if recipe_data.get("source_url"):
        description = f"Source: {recipe_data['source_url']}\n\n{description}"

    recipe_body = {
        "name": recipe_data["name"],
        "description": description,
        "servings": float(recipe_data.get("servings") or 4),
        "source_url": recipe_data.get("source_url") or "",
        "ingredients": ingredients,
    }

    resp = _api_post("recipes", recipe_body)
    recipe_id = resp.get("id")
    if not recipe_id:
        raise ValueError("Failed to create recipe in Storage")
    recipe_id = int(recipe_id)

    log.info("Created recipe '%s' (ID %d)", recipe_data["name"], recipe_id)

    # Upload image if available
    if recipe_data.get("image_url"):
        filename = _upload_recipe_image(recipe_id, recipe_data["image_url"])
        if filename:
            _api_put(f"recipes/{recipe_id}", {"picture_filename": filename})
            log.debug("Uploaded recipe image: %s", filename)

    return {"recipe_id": recipe_id, "name": recipe_data["name"]}


def _list_recipes() -> list[dict]:
    """List all recipes from Storage."""
    recipes = _api_get("recipes")
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "picture_filename": r.get("picture_filename"),
            "servings": r.get("servings", 1),
        }
        for r in recipes
    ]


def _get_recipe_detail(recipe_id: int) -> dict:
    """Get full recipe detail with ingredient stock status."""
    recipe = _api_get(f"recipes/{recipe_id}")

    # Storage returns ingredients with stock info included
    recipe_ingredients = recipe.get("ingredients", [])

    # Get stock info for additional status calculation
    stock = _api_get("stock")
    stock_by_product: dict[int, dict] = {}
    for s in stock:
        stock_by_product[s["product_id"]] = s

    # Get all products for parent lookups (include inactive parents so
    # recipe ingredients pointing to inactive group-master parents can be
    # resolved for unit info and stock aggregation).
    products_list = _api_get("products?active_only=false")
    products_by_id = {p["id"]: p for p in products_list}

    # Get all conversions for stock comparison
    all_conversions = _api_get("conversions")

    # Build parent→children map for stock aggregation
    children_of: dict[int, list[int]] = {}
    for p in products_list:
        ppid = p.get("parent_id")
        if ppid:
            children_of.setdefault(int(ppid), []).append(p["id"])

    ingredients = []
    for pos in recipe_ingredients:
        pid = pos.get("product_id")
        product = products_by_id.get(pid, {})
        product_name = pos.get("product_name") or product.get("name", f"Product #{pid}")
        needed = pos.get("amount") or 0
        recipe_unit_id = pos.get("unit_id")

        stock_entry = stock_by_product.get(pid)
        in_stock_pieces = 0
        amount_opened = 0
        if stock_entry:
            in_stock_pieces = stock_entry.get("amount", 0)
            amount_opened = stock_entry.get("amount_opened", 0)

        # "To taste" ingredients: green if any in stock, yellow if none
        if needed == 0:
            if in_stock_pieces > 0 or any(
                stock_by_product.get(cid, {}).get("amount", 0) > 0
                for cid in children_of.get(pid, [])
            ):
                status = "green"
            else:
                status = "yellow"
            ingredients.append({
                "id": pos.get("id"),
                "product_id": pid,
                "product_name": product_name,
                "parent_id": product.get("parent_id"),
                "parent_name": None,
                "amount_needed": 0,
                "unit_abbrev": "",
                "note": pos.get("note", ""),
                "status": status,
            })
            continue

        # Aggregate child stock for parent products
        child_stock_converted = None
        if in_stock_pieces == 0 and pid in children_of:
            for cid in children_of[pid]:
                cstock = stock_by_product.get(cid)
                if not cstock or cstock.get("amount", 0) == 0:
                    continue
                child_amount = cstock.get("amount", 0)
                child_product = products_by_id.get(cid, {})
                child_unit_id = child_product.get("unit_id")
                amount_opened += cstock.get("amount_opened", 0)
                if recipe_unit_id and child_unit_id:
                    converted = _convert_recipe_to_stock(
                        child_amount, child_unit_id, cid, recipe_unit_id,
                        all_conversions,
                    )
                    if converted is not None:
                        child_stock_converted = (child_stock_converted or 0) + converted
                        continue
                in_stock_pieces += child_amount

        # Get unit abbreviation
        unit_abbrev = pos.get("unit_abbreviation", "")
        stock_unit_id = product.get("unit_id")

        # Determine status using unit conversions
        if child_stock_converted is not None:
            if child_stock_converted >= needed:
                status = "green"
            else:
                status = "red"
        elif recipe_unit_id and stock_unit_id and recipe_unit_id != stock_unit_id:
            stock_in_recipe_units = _convert_recipe_to_stock(
                in_stock_pieces, stock_unit_id, pid, recipe_unit_id, all_conversions
            )
            if stock_in_recipe_units is not None:
                if stock_in_recipe_units >= needed:
                    status = "yellow" if in_stock_pieces <= 1 and amount_opened >= 1 else "green"
                else:
                    status = "red"
            else:
                if in_stock_pieces >= 1:
                    status = "yellow" if amount_opened >= 1 else "green"
                else:
                    status = "red"
        else:
            if in_stock_pieces >= needed:
                status = "yellow" if in_stock_pieces == 1 and amount_opened >= 1 else "green"
            else:
                status = "red"

        # Get parent product info
        parent_id = product.get("parent_id")
        parent_name = None
        if parent_id:
            parent = products_by_id.get(int(parent_id))
            if parent:
                parent_name = parent.get("name")

        ingredients.append({
            "id": pos.get("id"),
            "product_id": pid,
            "product_name": product_name,
            "parent_id": parent_id,
            "parent_name": parent_name,
            "amount_needed": needed,
            "unit_abbrev": unit_abbrev,
            "note": pos.get("note", ""),
            "status": status,
        })

    # Parse instructions from description
    description = recipe.get("description", "")
    source_url = recipe.get("source_url") or None
    instructions = []
    for line in description.split("\n"):
        line = line.strip()
        if line.startswith("Source: "):
            if not source_url:
                source_url = line[8:].strip()
        elif line:
            instructions.append(line)

    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "picture_filename": recipe.get("picture_filename"),
        "servings": recipe.get("servings", 1),
        "source_url": source_url,
        "ingredients": ingredients,
        "instructions": instructions,
    }


def _add_to_shopping_list(recipe_id: int, mode: str) -> dict:
    """Add recipe ingredients to shopping list.

    mode: "missing" | "all" | "missing_and_opened"

    Uses unit conversions to calculate purchase amounts in pieces.
    """
    detail = _get_recipe_detail(recipe_id)
    added = 0

    # Load conversions and products for smart amounts
    all_conversions = _api_get("conversions")
    products_list = _api_get("products")
    products_by_id = {p["id"]: p for p in products_list}

    # Get recipe ingredients for unit_id lookup
    recipe_data = _api_get(f"recipes/{recipe_id}")
    recipe_ingredients = recipe_data.get("ingredients", [])
    ing_by_id = {i["id"]: i for i in recipe_ingredients}

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
        pid = ing.get("parent_id") or ing.get("product_id")
        if not pid:
            continue
        pid = int(pid)

        # Calculate how many pieces to buy using conversions
        purchase_amount = 1
        pos = ing_by_id.get(ing.get("id"))
        if pos:
            recipe_unit_id = pos.get("unit_id")
            recipe_amount = pos.get("amount", 1)
            prod = products_by_id.get(pid, {})
            stock_unit_id = prod.get("unit_id")

            if recipe_unit_id and stock_unit_id and recipe_unit_id != stock_unit_id:
                amount_in_pieces = _convert_recipe_to_stock(
                    recipe_amount, recipe_unit_id, pid, stock_unit_id, all_conversions
                )
                if amount_in_pieces is not None and amount_in_pieces > 0:
                    purchase_amount = math.ceil(amount_in_pieces)
            elif recipe_unit_id == stock_unit_id:
                purchase_amount = math.ceil(recipe_amount)

        try:
            _api_post("shopping-list", {
                "product_id": pid,
                "amount": purchase_amount,
                "unit_id": ing.get("unit_id") or products_by_id.get(pid, {}).get("unit_id"),
                "note": ing.get("product_name", ""),
                "recipe_id": recipe_id,
            })
            added += 1
        except Exception as exc:
            log.warning("Failed to add product %d to shopping list: %s", pid, exc)

    return {"added": added, "mode": mode}


def _delete_recipe(recipe_id: int) -> None:
    """Delete a recipe from Storage (cascades ingredients automatically)."""
    # Fetch picture_filename before deleting so we can clean up the image file
    try:
        recipe = _api_get(f"recipes/{recipe_id}")
        picture_filename = recipe.get("picture_filename")
    except Exception:
        picture_filename = None

    _api_delete(f"recipes/{recipe_id}")

    if picture_filename:
        try:
            _api_delete(f"files/recipes/{picture_filename}")
            log.debug("Deleted recipe image: %s", picture_filename)
        except Exception as exc:
            log.warning("Failed to delete recipe image '%s': %s", picture_filename, exc)

    log.info("Deleted recipe ID %d", recipe_id)


# ---------------------------------------------------------------------------
# Full scrape pipeline
# ---------------------------------------------------------------------------
def _handle_scrape(url: str) -> dict:
    """Full pipeline: scrape URL → match products → discover missing → save to Storage."""
    log.info("Scraping recipe from: %s", url)

    # 0. Ensure standard units and conversions exist
    _ensure_units_and_conversions()

    # 1. Scrape the recipe
    recipe_data = _scrape_recipe(url)
    log.debug(
        "Extracted recipe: '%s' with %d ingredients",
        recipe_data.get("name"),
        len(recipe_data.get("ingredients", [])),
    )

    # 2. Get all active products and Group master products (including inactive stubs).
    # Group master products are used for matching so recipes always link to
    # category-level products; fetching inactive products prevents duplicate stubs.
    products = _get_all_products()
    group_master_id, group_master_products = _get_group_master_products()

    # 3. Match ingredients to products
    for ing in recipe_data.get("ingredients", []):
        match = _match_ingredient(ing["name"], products, group_masters=group_master_products)
        if match:
            ing["_product_id"] = match["id"]
            log.debug("Matched '%s' → '%s' (ID %d)", ing["name"], match["name"], match["id"])
        else:
            ing["_product_id"] = None

    # 4. AI-assisted matching for unmatched
    recipe_data["ingredients"] = _ai_match_ingredients(
        recipe_data.get("ingredients", []), products, group_masters=group_master_products
    )

    # 5. Discover missing products via scraper (batch-translated, parallel)
    unmatched = [
        i for i in recipe_data.get("ingredients", [])
        if i.get("_product_id") is None
    ]

    any_discovered = False
    if unmatched and _scraper_available():
        # Batch-translate all ingredient names to Finnish in one Gemini call
        names = [i["name"] for i in unmatched]
        translations = _batch_translate_to_finnish(names)

        # Discover all ingredients in parallel
        def _discover_one(ing: dict) -> tuple[dict, dict | None]:
            search_term = translations.get(ing["name"], ing["name"])
            return ing, _scraper_discover(ing["name"], search_term=search_term)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_discover_one, ing): ing for ing in unmatched}
            for fut in as_completed(futures):
                try:
                    ing, result = fut.result()
                    if result:
                        any_discovered = True
                except Exception as exc:
                    log.warning("Scraper discover thread failed: %s", exc)

        if any_discovered:
            # Refresh products and Group master products after discovers complete
            products = _get_all_products()
            group_master_id, group_master_products = _get_group_master_products()
            for ing in unmatched:
                if ing.get("_product_id") is not None:
                    continue
                match = _match_ingredient(ing["name"], products, group_masters=group_master_products)
                if match:
                    ing["_product_id"] = match["id"]
                    log.debug(
                        "Discovered and matched '%s' → '%s' (ID %d)",
                        ing["name"], match["name"], match["id"],
                    )

            # Re-run AI matching only if discovers succeeded
            still_unmatched_after_discover = [
                i for i in recipe_data.get("ingredients", [])
                if i.get("_product_id") is None
            ]
            if still_unmatched_after_discover:
                recipe_data["ingredients"] = _ai_match_ingredients(
                    recipe_data.get("ingredients", []), products, group_masters=group_master_products
                )

    # 6. Create stub parent products (group masters) for any still-unmatched ingredients
    stub_product_ids: set[int] = set()
    still_unmatched = [
        i for i in recipe_data.get("ingredients", [])
        if i.get("_product_id") is None
    ]

    # 6a. Deduplication pass — try normalized + AI matching before creating stubs
    if still_unmatched:
        _deduplicate_stub_candidates(
            still_unmatched, products, group_masters=group_master_products,
        )
        still_unmatched = [i for i in still_unmatched if i.get("_product_id") is None]

    if still_unmatched:
        # Look up valid location ID (unit resolved per-ingredient below)
        default_loc_id = None
        try:
            locs = _api_get("locations")
            if locs:
                default_loc_id = locs[0]["id"]
        except Exception:
            pass
        umap = _get_unit_map()
        default_unit_id = umap.get("g") or 1

        if default_loc_id is None:
            log.warning(
                "Cannot create stub products — no locations in Storage"
            )
        else:
            # Reuse group_master_id resolved above; create the group only if
            # it didn't exist yet at the start of this scrape.
            if group_master_id is None:
                try:
                    gm = _api_post("product-groups", {"name": "Group master"})
                    group_master_id = gm.get("id")
                except Exception as exc:
                    log.warning("Could not create 'Group master' group: %s", exc)

            for ing in still_unmatched:
                stub_name = ing["name"]
                log.warning(
                    "No existing product found for '%s' — creating stub parent product",
                    stub_name,
                )
                try:
                    # Resolve the ingredient's unit for the stub product
                    ing_unit_id = _resolve_unit_id(ing.get("unit"))
                    stub_body: dict = {
                        "name": stub_name,
                        "description": "Auto-created by recipe scraper",
                        "location_id": default_loc_id,
                        "unit_id": ing_unit_id or default_unit_id,
                        "default_best_before_days": 0,
                        "active": False,
                        "min_stock_amount": 0,
                    }
                    if group_master_id is not None:
                        stub_body["product_group_id"] = group_master_id
                    resp = _api_post("products", stub_body)
                    new_id = resp.get("id")
                    if new_id:
                        ing["_product_id"] = int(new_id)
                        stub_product_ids.add(int(new_id))
                        log.debug(
                            "Created stub parent product '%s' (ID %s, unit_id=%s, group master)",
                            stub_name, new_id, ing_unit_id or default_unit_id,
                        )
                except Exception as exc:
                    log.warning("Failed to create stub product '%s': %s", stub_name, exc)

    # 7. Create product-specific unit conversions via AI (skip stubs)
    products = _get_all_products()
    products_by_id = {p["id"]: p for p in products}
    try:
        _create_product_conversions(
            recipe_data["ingredients"], products_by_id,
            skip_product_ids=stub_product_ids,
        )
    except Exception as exc:
        log.warning("Failed to create product conversions: %s", exc)

    # 7b. Update product default units for products without conversions
    try:
        _update_product_default_units(recipe_data["ingredients"], products_by_id)
    except Exception as exc:
        log.warning("Failed to update product default units: %s", exc)

    # 7c. Create cross-domain density conversions (weight↔volume, skip stubs)
    # Refresh products_by_id to include any default-unit changes from 7b
    products = _get_all_products()
    products_by_id = {p["id"]: p for p in products}
    try:
        _ensure_density_conversions(
            recipe_data["ingredients"], products_by_id,
            skip_product_ids=stub_product_ids,
        )
    except Exception as exc:
        log.warning("Failed to create density conversions: %s", exc)

    # 8. Create recipe in Storage
    result = _create_recipe(recipe_data, recipe_data["ingredients"])
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
            # Provider-aware readiness check
            ai_ready = False
            if AI_PROVIDER == "ollama":
                ai_ready = bool(OLLAMA_URL)
            elif AI_PROVIDER == "claude":
                ai_ready = bool(CLAUDE_API_KEY)
            else:
                ai_ready = bool(GEMINI_KEY)
            return self._json({
                "configured": bool(STORAGE_URL and ai_ready),
                "ai_provider": AI_PROVIDER,
            })

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
    log.info("Storage URL: %s", STORAGE_URL)

    if STORAGE_URL:
        wait_for_storage(STORAGE_URL)

    if AI_PROVIDER == "ollama":
        log.info("AI provider: ollama (url=%s, model=%s)", OLLAMA_URL, OLLAMA_MODEL)
    elif AI_PROVIDER == "claude":
        log.info("AI provider: claude (model=%s)", CLAUDE_MODEL)
    else:
        log.info("AI provider: gemini (model=%s)", GEMINI_MODEL)

    server = _ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
