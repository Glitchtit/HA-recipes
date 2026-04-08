# Changelog

## 1.2.0

- Create stub parent products in Grocy for ingredients that cannot be found via scraper search, instead of failing the entire recipe
- Recipes now always save successfully even when some products are unavailable in grocery stores

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
