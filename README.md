<p align="center">
  <img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/recipes/logo.png" alt="Recipes" height="72">
</p>

<p align="center">
  <b>Import recipes from any web page and cook from your stock</b><br>
  Part of Glitchtit's HA apps — needs the Storage add-on.
</p>

<p align="center">
  <a href="https://github.com/Glitchtit/HA-apps"><img alt="Home Assistant add-on" src="https://img.shields.io/badge/Home%20Assistant-add--on-0047AB?style=flat-square&logo=homeassistant&logoColor=white"></a>
  <img alt="Version" src="https://img.shields.io/badge/dynamic/json?style=flat-square&color=FF4F00&label=version&url=https%3A%2F%2Fraw.githubusercontent.com%2FGlitchtit%2FHA-recipes%2Fmain%2Frecipes%2Fconfig.json&query=%24.version">
  <a href="https://hacs.xyz"><img alt="HACS custom" src="https://img.shields.io/badge/HACS-custom-1F2937?style=flat-square"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/docs/screenshots/hero.png" alt="Recipes screenshot" width="100%">
</p>

## ✨ Features

- **Import from any recipe page** — paste a URL and the AI reads the page, extracts the ingredients and steps, and translates ingredient names to Finnish. Lines it could not read are listed instead of dropped. Cloudflare-protected pages (k-ruoka.fi) work through an optional FlareSolverr
- **Ingredients matched to your products** — every ingredient line is bound to a Finnish product in [HA-storage](https://github.com/Glitchtit/HA-storage), so a recipe knows what is in stock. Tap an ingredient to fix a wrong match, amount or unit
- **See what you can cook** — recipe cards show ✓ when everything is in stock or "N missing" for the missing ingredients, cookable recipes first. Ingredient rows are coloured by stock status
- **Cook and shop** — cooking a recipe takes the ingredients out of stock for the servings you choose; "Add to shopping list" adds the missing ingredients (or all of them) to the shopping list
- **Sets** — named sets of products (e.g. "Taco night") stored in Storage, pushed onto the shopping list from a checklist; items already in stock or on the list start unchecked
- **Receipt printing** — print a recipe on a thermal receipt printer with [HA-print](https://github.com/Glitchtit/HA-print)
- **Choice of AI** — Gemini (default), Claude, or a local Ollama model

## 📸 Screenshots

<p align="center">
  <img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/docs/screenshots/phones.png" alt="Recipe detail and Sets on a phone" width="100%">
</p>

<p align="center"><sub>A recipe's ingredients and steps, and the Sets shopping bundles</sub></p>

## 🚀 Installation

### Add-on

1. In Home Assistant open **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add `https://github.com/Glitchtit/HA-apps`
2. Install **Storage** first, then **Recipe**
3. On the Configuration tab pick an `ai_provider` and set its key (or the Ollama URL). Add `flaresolverr_url` if you import from Cloudflare-protected sites
4. Start it and open **Recipe** from the sidebar

### Integration (HACS)

Optional. Adds actions that automations, scripts and voice assistants can call.

1. In HACS open **⋮ → Custom repositories**, add `https://github.com/Glitchtit/HA-recipes` with category **Integration**
2. Download **Recipes** and restart Home Assistant
3. **Settings → Devices & services → Add integration → Recipes** (the add-on's address is filled in from the Supervisor)

## ⚙️ Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `ai_provider` | `gemini` | `gemini`, `claude` or `ollama` |
| `gemini_api_key` / `gemini_model` | — / `gemini-2.0-flash` | Google Gemini API key and model |
| `claude_api_key` / `claude_model` | — / `claude-3-5-haiku-20241022` | Anthropic API key and model |
| `ollama_url` / `ollama_model` | — / `llama3` | Local Ollama server and model |
| `flaresolverr_url` | — | FlareSolverr address, e.g. `http://192.168.1.10:8191`, for Cloudflare-protected recipe sites |
| `debug` | `false` | Verbose logging |

## 🏠 In Home Assistant

The integration adds no entities. It registers three actions:

| Action | What it does |
|--------|--------------|
| `ha_recipes.search` | Find saved recipes by name |
| `ha_recipes.scrape_url` | Import a recipe from a URL (takes up to a minute) |
| `ha_recipes.suggest_from_stock` | Recipes you can cook from what is in stock, ones using soon-expiring food first |

All three return a response, so use them with `response_variable` in scripts.

## 🧩 The family

<p align="center">
  <a href="https://github.com/Glitchtit/HA-storage"><img src="https://raw.githubusercontent.com/Glitchtit/HA-storage/main/storage/icon.png" width="56" alt="Storage"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-scraper"><img src="https://raw.githubusercontent.com/Glitchtit/HA-scraper/main/addon/icon.png" width="56" alt="Scraper"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-stock"><img src="https://raw.githubusercontent.com/Glitchtit/HA-stock/main/stock/icon.png" width="56" alt="Stock"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-recipes"><img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/recipes/icon.png" width="56" alt="Recipes"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-chores"><img src="https://raw.githubusercontent.com/Glitchtit/HA-chores/main/chores/icon.png" width="56" alt="Chores"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-lists"><img src="https://raw.githubusercontent.com/Glitchtit/HA-lists/main/lists/icon.png" width="56" alt="Lists"></a>&nbsp;
  <a href="https://github.com/Glitchtit/HA-print"><img src="https://raw.githubusercontent.com/Glitchtit/HA-print/main/print/icon.png" width="56" alt="Print"></a>
</p>

<p align="center"><sub>Recipes keeps its recipes, products, stock and sets in Storage and adds what is missing to Storage's shopping list; Print turns a recipe into a receipt.</sub></p>

## 🛠️ Development

```bash
# Backend + integration tests
python -m pytest -q

# Frontend dev server
cd recipes/frontend && npm install && npm run dev
```
