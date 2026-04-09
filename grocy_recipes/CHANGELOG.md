# Changelog

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
