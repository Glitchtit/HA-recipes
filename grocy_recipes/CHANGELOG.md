## 1.5.21
- Fix: recipe stock status now includes inactive parent products in lookups — fixes all ingredients showing red after optimizer runs because parent products (active=0) were excluded from stock aggregation queries

## 1.5.20
- Fix: `/api/config` readiness check now provider-aware — correctly reports configured status for Ollama and Claude (was wrongly checking GEMINI_KEY for all providers)
- Stability: Storage health check now caps at 60 retries with degraded-state UI and manual retry button instead of silent infinite loop
- Stability: nginx proxy timeouts standardised — Storage API 120s read / 10s connect, file proxy 30s, scraper 120s, all with explicit connect timeouts

## 1.5.19
- Fix: `_resolve_unit_id("kpl")` now returns the actual kpl unit ID instead of None — directly fixes "1 lemon → 1 g" bug where countable items fell back to the product's default unit (grams)
- Fix: Stub products now use the ingredient's resolved unit (kpl for countable items) instead of always defaulting to Gramma (g)
- Fix: `_STANDARD_UNITS` key renamed from legacy `"description"` to `"abbreviation"` matching Storage API field
- Expanded `_COUNTABLE_KEYWORDS` from 20 to 40+ Finnish countable ingredients (fruits, vegetables, proteins, bread items)
- Raised `_fix_countable_units` threshold from 24 to 50 for broader safety net coverage
- Added deduplication safeguard before stub creation: normalized name matching + AI semantic matching against existing products to prevent duplicate stubs
- Strengthened AI prompts with more countable item examples, negative examples (weight-given items), and edge cases across all three languages

## 1.5.18
- Cleanup: removed stale Grocy references from backend docstrings and User-Agent strings

## 1.5.17
- All recipes now use the two-step summarize→extract pipeline regardless of JSON-LD — eliminates remaining cases where JSON-LD ingredient strings with parenthetical annotations (e.g. "2 ägg (ca 120 g)") caused wrong units

## 1.5.16
- Two-step recipe extraction: page is first summarized to strip parenthetical weight annotations (e.g. "2 ägg (ca 120 g)" → "2 ägg"), then structured JSON extracted from the clean summary — fixes "2 ägg → 2 g" bug
- Strengthened `_translate_ingredients` prompt: ignores parenthetical weight/calorie notes; maps "to taste" / "salta väl" / "maun mukaan" to amount=null, unit=null
- Post-processing safety net `_fix_countable_units()`: forces unit=kpl for known countable items (eggs, onions, potatoes, etc.) when AI returns g/kg with amount ≤ 24
- "To taste" ingredients now stored with amount=0; stock check shows green if any amount in stock, yellow if none — no longer stored as "1 unit"
- Frontend already hides amount/unit when amount_needed==0 — no display changes needed

## 1.5.15
- Recipe ingredient matching now restricted to products in the "Group master" product group (including inactive stubs) — resolves matching to specific branded products and eliminates duplicate stub creation when re-scraping the same recipe

## 1.5.14
- Recipe ingredient matching now always resolves to parent/category products (e.g. "Kananmunat") instead of specific brand variants — child products are excluded from the AI matching list so recipes reference the general category, not a particular brand

## 1.5.13
- Fix: AI ingredient parsing now correctly uses "kpl" for whole countable items with no unit (eggs/ägg, onion/lök, potato, etc.) instead of incorrectly inventing grams — both JSON-LD fast-path and full-page Gemini fallback updated

## 1.5.12
- Recipe stubs now created as inactive group-master parent products (product_group_id = "Group master", active = false)
- Stub parents are excluded from optimizer AI token budget and handled correctly by the group cleanup pass

## 1.5.11
- Persistent service health monitoring: background loop never stops; re-detects Storage/Scraper if they go down, reloads nginx only when URL changes

# Changelog

## 1.5.10
- Persistent service probing: if Storage addon is not found, retry every 5 s before starting (up to 100 s); if Scraper not found, retry every 30 s in background

## 1.5.9
- Fix Claude JSON parsing: add `_extract_json_text()` helper to extract JSON from
  markdown-fenced or prose-prefixed Claude responses; fixes "Expecting value" parse errors

## 1.5.8
- Add Claude AI provider support: `claude_api_key` + `claude_model` in config.json, run script, backend.py globals, `_call_claude_json()`, and `_call_ai_json()` dispatcher

## 1.5.7
- Recipe now has its own AI config (ai_provider, gemini_api_key, gemini_model,
  ollama_url, ollama_model) directly in addon config — no longer fetches from Storage
- Removed _fetch_ai_key_from_storage() — AI provider/key/model read from env vars at startup

## 1.5.6
- Fix startup log: now shows actual AI provider (gemini/ollama) and model/URL
  after fetching config from Storage instead of always logging "Gemini model: ..."

## 1.5.5
- Log AI token usage after every successful AI call:
  Gemini: prompt/output/total token counts; Ollama: prompt/output tokens + total duration (ms)

## 1.5.4
- Add Ollama support as an alternative AI provider for recipe scraping
- `_fetch_ai_key_from_storage()` now calls `/api/config/ai` and populates
  `AI_PROVIDER`, `OLLAMA_URL`, `OLLAMA_MODEL` globals
- Add `_call_ollama_json()` and `_call_ai_json()` dispatcher; all AI call sites
  now route to Gemini or Ollama based on the configured provider

## 1.5.3

- Fix Gemini DEADLINE_EXCEEDED timeouts when scraping recipes:
  - JSON-LD fast path: extract schema.org/Recipe structured data directly from page HTML (works for most modern recipe sites like koket.se, zeinaskitchen.se). Only a small focused Gemini call is needed for ingredient translation to Finnish — much faster and more reliable than sending full page text.
  - Full-page fallback text trimmed from 15,000 to 8,000 characters for sites without JSON-LD.
  - Retry backoff for DEADLINE_EXCEEDED/504 increased to 30 seconds (from 2–4 s).
  - Max retries increased from 3 to 4.
  - Client-side HTTP timeout increased from 120 s to 300 s.

## 1.5.2

- Fix recipe image duplication: filenames now include a random token to prevent collisions when recipe IDs are reused after factory reset
- Delete recipe image file from Storage when a recipe is deleted
- Log image URLs during upload for easier debugging

## 1.5.1

- Fix API proxy: nginx static asset regex no longer intercepts /api/ image and file requests

## 1.5.0

- Auto-detect Storage URL from container hostname and Supervisor API
- storage_url config now optional (auto-detected when not set)
- Fix recipe image upload: resolve protocol-relative and relative URLs
- Validate Content-Type before uploading images (reject non-image responses)
- Pass actual content-type to Storage instead of hardcoded application/octet-stream

## 1.4.2

- Backend waits for Storage health check on startup before serving requests
- Frontend shows waiting state with spinner until Storage is reachable
- Renamed addon display name from "Grocy Recipes" to "Recipe"

## 1.4.1

- Fetch Gemini AI key from Storage addon (`GET /api/config/ai-key`) instead of requiring local config
- Local `gemini_api_key` and `gemini_model` are now optional overrides
- Retry logic (up to 30 attempts, 5s apart) for Storage connectivity on startup

## 1.4.0

- Replaced Grocy API with HA-Storage API
- Simplified recipe creation (single API call with ingredients)
- Simplified unit handling (single unit_id per product)
- Updated product field names
- Updated nginx proxy configuration

## 1.3.6

- Major performance improvement: recipe scraping reduced from ~3 min to ~45s
- Batch-translate all ingredient names to Finnish in one Gemini call instead of N sequential calls
- Parallel scraper discovery using ThreadPoolExecutor (max 4 concurrent searches)
- Skip stub products in package-size and density conversion analysis (eliminates primary 504 timeout cause)
- Skip redundant second AI match pass when no scraper discoveries succeed
- Graceful handling when grocy_scraper addon is unavailable (no longer blocks recipe creation)

## 1.3.5

- Propagate density conversions from parent products to all child products (Grocy does not inherit product-specific conversions)
- Self-healing density conversions: when a recipe uses a unit in a different domain (e.g., dl) than the matched product (e.g., kg), automatically create weight↔volume conversions via Gemini AI density estimation
- New `_ensure_density_conversions()` runs after product conversion setup during recipe scraping
- Adds `_derive_density_conversions()` to generate all weight↔volume pairs from a single primary density

## 1.3.3

- Fix "kananmuna" (egg) QU constraint error: count/piece units (kpl, st, pcs) now use the product's stock QU directly instead of resolving to a separate Kappale unit
- Fix scrape timeout: increase nginx backend proxy timeout from 180s to 600s to handle slow Gemini API calls
- Add 120s timeout to Gemini API client to prevent infinite hangs
- Handle BrokenPipeError gracefully when client disconnects during long scrapes

## 1.3.2

- Fix recipe ingredient creation failing with "Provided qu_id doesn't have a related conversion for that product"
- For products without a detectable package size (e.g. "Turskafile"), automatically update the product's default unit to the recipe unit (e.g. grams) instead of leaving it as Piece
- Products WITH detectable sizes (e.g. "Maito 1L") still use Piece as stock unit with AI-created conversions

## 1.3.1

- Wire up debug toggle: set `debug: true` in add-on config to enable verbose logging
- Suppress routine HTTP request logs (config/recipes polling) in normal mode — only important events shown
- Suppress nginx access logs for routine polling endpoints in normal mode
- Backend startup now logs debug mode status

## 1.3.0

- Automated unit handling: auto-create standard recipe units in Grocy (g, kg, dl, l, ml, tl, rkl, rs, kpl) and global conversions (1 l = 10 dl, 1 kg = 1000 g, etc.)
- Recipe positions now use correct units (e.g. "600 g" instead of "600 Piece")
- AI-powered product package size detection: Gemini analyses product names to create Grocy unit conversions (e.g. "Maito 1L" → 1 piece = 1 litre)
- Conversion-aware stock comparison: recipe amounts are compared against stock using unit conversions for accurate green/yellow/red status
- Smart shopping list: calculates purchase amounts in pieces using unit conversions (e.g. 8 dl milk → 1 piece)
- Ingredient display shows amount with unit (e.g. "Maito — 8 dl") instead of meaningless piece counts

## 1.2.3

- Fix stub product creation: include all required Grocy fields (qu_id_consume, qu_id_price, default_best_before_days) to prevent 400 errors

## 1.2.2

- Fix stub product creation: query valid QU and location IDs from Grocy instead of hardcoding ID 1
- Fix recipe position linking: fall back to first valid QU when product's qu_id_stock is invalid
- Add detailed error logging for failed recipe ingredient linking (shows Grocy response body)

## 1.2.1

- Fix recipe ingredient linking: use each product's own qu_id_stock for recipe positions instead of a global lookup that caused 400 errors

## 1.2.0

- Create stub parent products in Grocy for ingredients that cannot be found via scraper search, instead of failing the entire recipe
- Recipes now always save successfully even when some products are unavailable in grocery stores
- Fix product creation payload to match Grocy API requirements
- Fix recipe_id type conversion to prevent logging errors

## 1.1.1

- Add connection keep-alive heartbeat to prevent Cloudflare 524 timeout when page is open for extended periods
- Show reconnect banner with reload button when connection is lost

## 1.1.0

- Fix multilingual recipe support: ingredient names are now always translated to Finnish regardless of recipe language (Swedish, English, etc.)
- Remove dangerous substring matching that caused false positives (e.g. "salt" matching "Lay's Chips Salted")
- Add AI-powered Finnish translation for scraper product discovery
- Improve AI ingredient matching with strict rules against brand-name false positives
- Re-run AI matching after product discovery for better results
- Multilingual household context: Swedish/Finnish/English input, Finnish products

## 1.0.1

- Fix recipe instructions rendering raw HTML tags instead of formatted text
- Add repository.json for HA Supervisor addon discovery

## 1.0.0

- Initial release
- AI-powered recipe scraping from URLs using Google Gemini
- Automatic ingredient matching to Grocy products (prefers parent products)
- Missing product discovery via grocy-scraper addon integration
- Recipe list with images and names
- Recipe detail view with stock status colors (green/yellow/red)
- Smart "Add to shopping list" with missing/all/opened options
- Auto-detection of grocy-scraper addon
