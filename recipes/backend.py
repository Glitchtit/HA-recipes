"""HA Recipes — Python backend server.

Handles recipe scraping via Gemini AI, product matching against Storage,
missing-product discovery via the scraper addon, and recipe CRUD.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
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
# Optional FlareSolverr instance for Cloudflare-fronted recipe sites (k-ruoka.fi).
FLARESOLVERR_URL: str = os.environ.get("FLARESOLVERR_URL", "").strip().rstrip("/")

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


def _claude_response_text(response) -> str:
    """Join the text blocks of a Claude response.

    Newer models think by default, so content[0] may be a thinking block.
    """
    if response.stop_reason == "max_tokens":
        log.warning("Claude response hit max_tokens; output may be truncated")
    return "".join(b.text for b in response.content if b.type == "text")


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
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            usage = response.usage
            log.info(
                "Claude usage — input tokens: %s, output tokens: %s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
            )
            text = _claude_response_text(response)
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
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            return _claude_response_text(response)
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
# Recipe scraping (Gemini AI)
# ---------------------------------------------------------------------------
_FETCH_UA = "Mozilla/5.0 (compatible; RecipeBackend/1.0)"


def _is_cloudflare_block(resp) -> bool:
    """True when *resp* is a Cloudflare challenge/block rather than a real 403."""
    if resp.status_code not in (403, 503):
        return False
    headers = {k.lower(): v for k, v in resp.headers.items()}
    return bool(headers.get("cf-mitigated")) or "cloudflare" in headers.get("server", "").lower()


def _fetch_via_flaresolverr(url: str) -> str:
    """Fetch *url* through FlareSolverr and return the rendered HTML.

    FlareSolverr solves the Cloudflare challenge in a headless browser and hands
    back the final page, so no cookie juggling is needed on our side.
    """
    base = FLARESOLVERR_URL
    endpoint = base if base.endswith("/v1") else f"{base}/v1"
    log.info("Cloudflare challenge on %s — fetching via FlareSolverr at %s", url, endpoint)
    r = requests.post(
        endpoint,
        json={"cmd": "request.get", "url": url, "maxTimeout": 90000},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr could not fetch page: {data.get('message') or data}")
    solution = data.get("solution") or {}
    status = solution.get("status")
    if status and int(status) >= 400:
        raise RuntimeError(f"FlareSolverr fetched page but site answered HTTP {status}")
    html = solution.get("response") or ""
    if not html:
        raise RuntimeError("FlareSolverr returned an empty page")
    return html


def _fetch_html(url: str) -> str:
    """Fetch a page's HTML, routing Cloudflare-blocked sites through FlareSolverr."""
    r = requests.get(url, timeout=15, headers={"User-Agent": _FETCH_UA})
    if _is_cloudflare_block(r):
        if FLARESOLVERR_URL:
            return _fetch_via_flaresolverr(url)
        host = urlparse(url).netloc or url
        raise RuntimeError(
            f"{host} is protected by Cloudflare and blocked the request. "
            "Set flaresolverr_url in the add-on options to scrape this site."
        )
    r.raise_for_status()
    return r.text


def _fetch_page(url: str) -> str:
    """Fetch a web page and return cleaned text content."""
    soup = BeautifulSoup(_fetch_html(url), "html.parser")
    # Remove script/style tags
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:15000]


def _extract_image_url(url: str, html: str | None = None) -> str | None:
    """Try to extract the main recipe image from the page."""
    try:
        if html is None:
            html = _fetch_html(url)
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


_LDJSON_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _find_jsonld_recipe(html: str) -> dict | None:
    """The page's schema.org Recipe object from its JSON-LD blocks, if any."""
    for block in _LDJSON_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else [data]
        nodes = [n for item in nodes if isinstance(item, dict)
                 for n in ([item] + [g for g in item.get("@graph", []) if isinstance(g, dict)])]
        for node in nodes:
            kind = node.get("@type")
            if kind == "Recipe" or (isinstance(kind, list) and "Recipe" in kind):
                return node
    return None


def _jsonld_servings(html: str) -> int | None:
    """Servings from the page's recipeYield ("8, 8 annosta", ["4", "4
    portioner"], 6); None when the page has no recipe data or no number.
    Pages often show the count only inside a portion input, which the text
    the AI reads does not contain."""
    recipe = _find_jsonld_recipe(html)
    value = recipe.get("recipeYield") if recipe else None
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) or None
    match = re.search(r"\d+", value) if isinstance(value, str) else None
    return int(match.group()) or None if match else None


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
    """Post-processor: force unit=kpl for countable items when AI returns grams with small count.

    Prevents "2 kananmuna" being stored as "2 g kananmuna" which is nonsensical.
    Only triggers when amount <= 50 (larger values are plausible weights).
    Kilograms are left alone: "1 kg perunoita" is a real weight.
    """
    for ing in ingredients:
        if not isinstance(ing, dict):
            continue
        name = (ing.get("name") or "").lower()
        unit = (ing.get("unit") or "").lower()
        amount = ing.get("amount")
        if unit == "g" and amount is not None and float(amount) <= 50:
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
   - PRESERVE specific variants verbatim: write "parmesan", "gouda", "juustoraaste", "fetajuusto", "mozzarella", "ruisjauho", "ekstraneitsytoliiviöljy", "merisuolahiutaleet", "syltsocker", "vaniljsocker", "florsocker", "farinsocker", "vispgrädde", "matlagningsgrädde", "gräddfil", "margarin", etc. — do NOT collapse them to a generic ("cheese", "flour", "oil", "salt", "socker", "smör", "grädde"). Translation to Finnish happens in the next step; here we just want the exact variant name from the source preserved.
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
    {{"name": "generic Finnish ingredient name", "specific": "specific Finnish variant or null", "amount": <number or null>, "unit": "unit string or null", "note": "any note like 'chopped' or null", "raw": "the ingredient line exactly as written in the summary", "source_name": "the ingredient name exactly as written in the summary, without amount, unit or note"}}
  ],
  "instructions": ["Step 1 text", "Step 2 text", ...]
}}

CRITICAL LANGUAGE RULES:
- ALWAYS translate ingredient names to Finnish, regardless of the source language.
  Examples: "smör" → "voi", "butter" → "voi", "torskfilé" → "turskafile", "salt" → "suola",
  "milk" → "maito", "ägg" → "kananmuna", "flour" → "vehnäjauho", "potatis" → "peruna",
  "onion" → "sipuli", "garlic" → "valkosipuli", "carrot" → "porkkana"
- "name" is the GENERIC category in Finnish (e.g. "kananmuna" not "3 kananmunaa", "juusto" not "Parmesan-juusto")
- "specific" is the Finnish-translated variant name when the recipe names a sub-type the household would not treat as interchangeable; otherwise null.
- The recipe name stays in the original language of the source page.
- Instructions: ALWAYS translate to English, regardless of the source language. Each step in the "instructions" array must read as natural English prose. Keep Finnish/Swedish ingredient names referenced inside the step (e.g. "Mix the vehnäjauho and bakpulver") OPTIONAL — prefer the English term ("Mix the flour and baking powder") for the cleanest reading. Do not include numbering prefixes ("1. ", "Step 1:"); the frontend renders the order.

SPECIFICITY RULES (CRITICAL — drives stock matching):
- Set "specific" to null when the source uses a plain generic term: "cheese", "juustoa", "ost", "salt", "flour", "oil", "butter", "milk", "sokeri", "socker", "strösocker", "sugar", "smör", "pasta", "ruisleipä".
- Set "specific" to the Finnish variant when the source names a non-interchangeable sub-type. Reasoning test: if swapping the variant for the plain generic would change the recipe outcome (different texture, flavor, chemistry, fat content), set "specific". Be conservative — when in doubt, set "specific" rather than null.
  * Cheese variants: parmesan, gouda, mozzarella, fetajuusto, juustoraaste, halloumi, cheddar, edam, brie, sinihomejuusto
  * Salt: merisuola, merisuolahiutaleet (vs. plain suola → null)
  * Flour: ruisjauho, grahamjauho, mantelijauho, kookosjauho, speltijauho (vs. plain vehnäjauho or "flour" → null)
  * Oil: oliiviöljy, rypsiöljy, seesamiöljy, kookosöljy (vs. plain "öljy" → null)
  * Pasta shapes: spaghetti, penne, fusilli, makaroni (vs. plain "pasta" → null)
  * Rice: basmati, arborio, jasmiiniriisi (vs. plain "riisi" → null)
  * Sugar variants: tomusokeri (powdered / florsocker / florsukker), fariinisokeri (brown / farinsocker / brunt socker), ruokosokeri (cane / rörsocker), hillosokeri (JAM SUGAR — Swedish "syltsocker" / English "jam sugar" / contains pectin), vaniljasokeri (VANILLA SUGAR — Swedish "vaniljsocker" / "vaniljsukker" / English "vanilla sugar"). Plain "sokeri" / "socker" / "sugar" / "strösocker" → null.
  * Butter & fats: margariini (margarine / margarin), suolaton voi (unsalted butter / osaltat smör). Plain "voi" / "smör" / "butter" → null.
  * Cream & dairy: vispikerma (whipping cream / vispgrädde), ruokakerma (cooking cream / matlagningsgrädde), smetana (sour cream / gräddfil), crème fraîche. Plain "kerma" / "grädde" / "cream" → null.
  * Milk fat levels are interchangeable in cooking → name "maito", specific null: täysmaito, kevytmaito, rasvaton maito, standardmjölk, mellanmjölk, lättmjölk, whole / low-fat / skim milk (a brand name like "Arla Ko®" does not make it specific).
  * Bread: ruisleipä, vaalealeipä (vs. plain "leipä" → null)
- Examples:
  "freshly grated parmesan" → {{"name": "juusto", "specific": "parmesan"}}
  "2 dl mjölk" → {{"name": "maito", "specific": null}}
  "1 msk olivolja" → {{"name": "öljy", "specific": "oliiviöljy"}}
  "salt" → {{"name": "suola", "specific": null}}
  "merisuolahiutaleet" → {{"name": "suola", "specific": "merisuolahiutaleet"}}
  "200 g spaghetti" → {{"name": "pasta", "specific": "spaghetti"}}
  "1 dl strösocker" → {{"name": "sokeri", "specific": null}}
  "1 dl syltsocker" → {{"name": "sokeri", "specific": "hillosokeri"}}
  "2 tsk vaniljsocker" → {{"name": "sokeri", "specific": "vaniljasokeri"}}
  "2 dl vispgrädde" → {{"name": "kerma", "specific": "vispikerma"}}
  "1 msk margarin" → {{"name": "voi", "specific": "margariini"}}

"ONE INGREDIENT PER ITEM" RULE:
- A line naming several things at once ("to taste suolaa ja pippuria", "salt and pepper", "salt, peppar och timjan") → a separate ingredient object for each thing, every one with the same "raw" line. Never one ingredient named "suola ja pippuri".

"TO TASTE" RULE:
- If a line in the summary starts with "to taste" (e.g. "to taste salt", "to taste pepper")
  → set amount=null and unit=null for that ingredient
  Example: "to taste salt" → {{"name": "suola", "amount": null, "unit": null}}

UNIT RULES (CRITICAL — follow these exactly):
- If the source already has a unit (dl, ml, cl, l, g, kg, tsk, msk, krm, tbsp, tsp) → translate to Finnish abbreviation (dl, ml, cl, l, g, kg, tl, rkl, mm)
- Imperial units (cup, oz, lb) → keep them as written; they are converted later
- Package words are units too → translate to the Finnish abbreviation: paket / förpackning / pack / paketti → "pkt", burk / jar / purkki → "prk", can / tin / tölkki → "tlk", påse / bag / pussi → "pss", ask / box / rasia → "rasia", knippe / bunch / nippu → "nippu", kruka / pot / ruukku → "ruukku". NEVER replace a package word with g or kg.
- If NO unit and ingredient is a whole countable item → unit = "kpl"
  Countable items include: eggs, onions, potatoes, carrots, lemons, limes, oranges, bananas, avocados, apples, pears, bell peppers, tomatoes, cucumbers, zucchini, eggplants, chicken breasts/thighs/legs, steaks, sausages, tortillas, bread rolls, garlic cloves, chili peppers
- If NO unit and ingredient is not countable (salt, pepper, oil, butter, flour, sugar, etc.) → unit = null
- NEVER invent a weight (g/kg) when no unit appears in the source line
- Examples:
  "2 ägg" → unit="kpl"  |  "3 eggs" → unit="kpl"  |  "1 lök" → unit="kpl"
  "1 chicken breast" → unit="kpl"  |  "4 potatis" → unit="kpl"
  "1 zucchini" → unit="kpl"  |  "2 tortillas" → unit="kpl"
  "500 g chicken" → unit="g" (weight given — NOT kpl)
  "1 burk crème fraiche" → unit="prk"  |  "1 paket smördeg" → unit="pkt"  |  "1 påse riven ost" → unit="pss"
  "2 dl cream" → unit="dl" (volume given — NOT kpl)
  "salt" → unit=null  |  "pepper to taste" → unit=null

- Do NOT include any text outside the JSON object"""

    result = _call_ai_json(prompt)
    if not result or not isinstance(result, dict):
        raise ValueError("Failed to extract recipe JSON from summary")

    if "ingredients" in result and isinstance(result["ingredients"], list):
        result["ingredients"] = _split_combined(_fix_countable_units(result["ingredients"]))

    if "instructions" in result and isinstance(result["instructions"], list):
        result["instructions"] = _strip_instruction_numbering(result["instructions"])

    result["source_url"] = url
    result["image_url"] = image_url
    return result


_COMBINED_RE = re.compile(r"\s*(?:,|&|\+|\bja\b|\boch\b|\band\b)\s*", re.IGNORECASE)


def _split_combined(ingredients: list) -> list:
    """One line per seasoning: a to-taste line the AI still returned as one
    ingredient ("suola ja pippuri") becomes one ingredient per item, each with
    the line's raw text and note. A measured line stays whole: its amount
    cannot be shared out, and "juusto ja kinkku" may be one product."""
    out: list = []
    for ing in ingredients:
        name = ing.get("name") if isinstance(ing, dict) else None
        parts = [p for p in _COMBINED_RE.split(name or "") if p.strip()]
        if len(parts) < 2 or ing.get("amount"):
            out.append(ing)
            continue
        sources = [p for p in _COMBINED_RE.split(ing.get("source_name") or "") if p.strip()]
        for i, part in enumerate(parts):
            out.append({**ing, "name": part.strip(), "specific": None,
                        "source_name": sources[i].strip() if len(sources) == len(parts) else None})
    return out


def _scrape_recipe(url: str) -> dict:
    """Scrape a recipe from URL using a two-step AI pipeline.

    Step 1 — summarize: AI normalizes the raw page text (strips parenthetical
    weight annotations, marks 'to taste' ingredients) into a clean recipe summary.
    Step 2 — extract: AI extracts structured Finnish JSON from the clean summary.

    All recipes go through both steps regardless of whether JSON-LD is present,
    ensuring consistent ingredient parsing quality.

    Returns: {name, image_url, servings, source_url, ingredients: [{name, amount, unit, note}], instructions: [str]}
    """
    raw_html = _fetch_html(url)
    image_url = _extract_image_url(url, raw_html)

    page_text = BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)[:8000]

    log.info("Summarizing recipe page for %s", url)
    summary = _summarize_recipe(page_text, url)
    if not summary:
        raise ValueError("Failed to summarize recipe page")

    recipe = _extract_recipe_from_summary(summary, url, image_url)
    servings = _jsonld_servings(raw_html)
    if servings:
        recipe["servings"] = servings
    return recipe



# ---------------------------------------------------------------------------
# Ingredient resolution (Storage owns matching, concepts and units)
# ---------------------------------------------------------------------------
def _resolve_ingredients(ingredients: list[dict]) -> list[dict]:
    """Ask Storage which concept each parsed line is (POST ingredients/resolve).

    Storage owns matching, concept creation, aliases and unit parsing, so the
    importer never creates products or guesses units. Returns one result per
    line, in order: {product_id, specificity, amount, unit_id, needs_review};
    product_id is None for a line Storage could not bind.
    """
    if not ingredients:
        return []
    items = [
        {
            "raw": ing.get("raw"),
            "name": ing.get("name"),
            "specific": ing.get("specific"),
            "amount": ing.get("amount"),
            "unit_text": ing.get("unit"),
            "source_name": ing.get("source_name"),
        }
        for ing in ingredients
    ]
    resolved = _api_post("ingredients/resolve", {"items": items}, timeout=300).get("items", [])
    if len(resolved) != len(ingredients):
        raise ValueError("Storage resolved a different number of ingredients")
    return resolved


def _ingredient_row(ing: dict, res: dict, idx: int) -> dict:
    """Storage recipe-ingredient payload for one resolved line."""
    note_parts = [part for part in (ing.get("note"), ing.get("name")) if part]
    specificity = res.get("specificity")
    return {
        "product_id": int(res["product_id"]),
        "amount": float(res.get("amount") or 0),
        "unit_id": int(res["unit_id"]),
        "note": " — ".join(note_parts),
        "sort_order": idx,
        "specificity": specificity if specificity in ("strict", "loose") else "loose",
        "raw_text": ing.get("raw") or None,
        "needs_review": bool(res.get("needs_review")),
    }


def _ingredient_rows(ingredients: list[dict], resolved: list[dict]) -> tuple[list[dict], list[str]]:
    """Storage payloads for the bound lines, and the source text of the lines
    Storage could not bind (reported to the user instead of silently dropped)."""
    rows, skipped = [], []
    for idx, (ing, res) in enumerate(zip(ingredients, resolved)):
        if not res.get("product_id"):
            line = ing.get("raw") or ing.get("name") or ""
            log.warning("Skipping an ingredient Storage could not bind: %r", line)
            skipped.append(line)
            continue
        rows.append(_ingredient_row(ing, res, idx))
    return rows, skipped


_INSTRUCTION_NUMBERING_RE = re.compile(r"^\s*\d+[.)\-]\s+")


def _strip_instruction_numbering(instructions: list) -> list[str]:
    """Strip leading ordinal prefixes ('1. ', '2) ', '3- ') from instructions.

    The summarize prompt asks the AI to number steps so it keeps the order;
    the extract step then carries those prefixes into the instructions array.
    Frontends render the array as `<ol>`, so the prefixes show up duplicated.
    """
    out: list[str] = []
    for s in instructions:
        if isinstance(s, str):
            out.append(_INSTRUCTION_NUMBERING_RE.sub("", s, count=1).strip())
        else:
            out.append(s)
    return out


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


def _create_recipe(recipe_data: dict, resolved: list[dict]) -> dict:
    """Create a recipe in Storage with all its resolved ingredients (one POST).
    Returns the created recipe's id and name, and the lines left out."""
    ingredients, skipped = _ingredient_rows(recipe_data.get("ingredients", []), resolved)

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

    return {"recipe_id": recipe_id, "name": recipe_data["name"], "skipped": skipped}


def _list_recipes() -> list[dict]:
    """List all recipes from Storage, with the ingredient status counts the
    list badges use (None from a Storage older than 0.27.0)."""
    recipes = _api_get("recipes")
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "picture_filename": r.get("picture_filename"),
            "servings": r.get("servings", 1),
            "ingredient_count": r.get("ingredient_count"),
            "missing_count": r.get("missing_count"),
            "yellow_count": r.get("yellow_count"),
        }
        for r in recipes
    ]


def _get_recipe_detail(recipe_id: int) -> dict:
    """Recipe detail for the frontend. Availability status is computed by
    Storage (GET recipes/{id}/availability) — single source of truth."""
    recipe = _api_get(f"recipes/{recipe_id}")

    by_ingredient: dict[int, dict] = {}
    try:
        avail = _api_get(f"recipes/{recipe_id}/availability")
        by_ingredient = {
            a["ingredient_id"]: a for a in avail.get("ingredients", [])
        }
    except Exception as exc:
        log.warning("Availability fetch failed for recipe %d: %s", recipe_id, exc)

    ingredients = []
    for pos in recipe.get("ingredients", []):
        a = by_ingredient.get(pos.get("id"), {})
        ingredients.append({
            "id": pos.get("id"),
            "product_id": pos.get("product_id"),
            "product_name": pos.get("product_name") or a.get("product_name", ""),
            "parent_id": a.get("parent_id"),
            "parent_name": None,
            "amount_needed": pos.get("amount") or 0,
            "unit_id": pos.get("unit_id"),
            "unit_abbrev": pos.get("unit_abbreviation", ""),
            "note": pos.get("note", ""),
            "specificity": pos.get("specificity", "loose"),
            "status": a.get("status", "red"),
            "available": a.get("available"),
            "approx": bool(a.get("approx")),
            "raw_text": pos.get("raw_text"),
            "needs_review": bool(pos.get("needs_review")),
        })

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
    """Add recipe ingredients to the shopping list.

    mode: "missing" | "all" | "missing_and_opened"

    Storage owns the stock and unit math (POST recipes/{id}/to-shopping): it
    adds each shortfall on the ingredient's own product in recipe units and
    merges repeated needs, so this is a thin delegate.
    """
    result = _api_post(f"recipes/{recipe_id}/to-shopping", {"mode": mode})
    return {"added": int(result.get("added", 0)), "mode": mode}


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
    """Full pipeline: scrape the page, let Storage resolve every ingredient
    line into a concept, then save the recipe."""
    log.info("Scraping recipe from: %s", url)
    recipe_data = _scrape_recipe(url)
    log.debug(
        "Extracted recipe: '%s' with %d ingredients",
        recipe_data.get("name"),
        len(recipe_data.get("ingredients", [])),
    )
    resolved = _resolve_ingredients(recipe_data.get("ingredients", []))
    result = _create_recipe(recipe_data, resolved)
    _reconcile_after_import()
    return result


def _handle_reimport(recipe_id: int) -> dict:
    """Fetch a recipe's source page again and replace its ingredients and
    servings through Storage. The recipe id, name, picture and instructions
    stay; Storage keeps the replaced ingredient rows in a backup table."""
    recipe = _api_get(f"recipes/{recipe_id}")
    url = (recipe.get("source_url") or "").strip()
    if not url:
        raise ValueError("This recipe has no source address to re-import from")
    log.info("Re-importing recipe %d from %s", recipe_id, url)
    recipe_data = _scrape_recipe(url)
    ingredients = recipe_data.get("ingredients", [])
    rows, skipped = _ingredient_rows(ingredients, _resolve_ingredients(ingredients))
    if not rows:
        raise ValueError("The re-import found no ingredients; the recipe was left unchanged")
    _api_put(f"recipes/{recipe_id}/ingredients", {"ingredients": rows})
    if recipe_data.get("servings"):
        _api_put(f"recipes/{recipe_id}", {"servings": float(recipe_data["servings"])})
    _reconcile_after_import()
    return {
        "recipe_id": recipe_id,
        "name": recipe.get("name"),
        "ingredients": len(rows),
        "needs_review": sum(1 for r in rows if r["needs_review"]),
        "skipped": skipped,
    }


def _reconcile_after_import() -> None:
    """Best effort: Storage's reconcile sweep merges twin concepts and links
    loose products."""
    try:
        _api_post("products/reconcile", timeout=120)
    except Exception as exc:
        log.warning("Post-import reconcile sweep failed: %s", exc)


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

        # Re-import: /api/recipe/<id>/reimport (same single-flight lock as scrape)
        m = re.match(r"/api/recipe/(\d+)/reimport$", path)
        if m:
            if not _op_lock.acquire(blocking=False):
                return self._json(
                    {"success": False, "error": "Another operation is in progress"},
                    409,
                )
            try:
                result = _handle_reimport(int(m.group(1)))
                return self._json({"success": True, **result})
            except BrokenPipeError:
                log.info("Client disconnected before re-import response was sent")
                return None
            except Exception as exc:
                log.exception("Re-import failed")
                try:
                    return self._json({"success": False, "error": str(exc)}, 500)
                except BrokenPipeError:
                    return None
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
