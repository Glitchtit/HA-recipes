<img src="https://raw.githubusercontent.com/Glitchtit/HA-recipes/main/recipes/logo.png" alt="Recipes" height="64">

AI-powered recipe import for Home Assistant. Paste a recipe URL, get the recipe with every ingredient matched to a real product in [HA-storage](https://github.com/Glitchtit/HA-storage), so it can go straight onto the shopping list.

Part of [Glitchtit's HA apps](https://github.com/Glitchtit/HA-apps). Needs the Storage add-on.

## Features

- **Import from any recipe page**: the AI reads the page, extracts the ingredients and steps, and translates them if needed. Cloudflare-protected pages (k-ruoka.fi) work through an optional FlareSolverr
- **Ingredients matched to your products**: every ingredient line is linked to a Finnish Storage product, so recipes know what is in stock and what to buy
- **Cook from stock**: see which recipes you can make right now, with the ones that use soon-expiring food first
- **Shopping list and printing**: add a recipe's missing ingredients to the shopping list, or print the recipe on a receipt printer with [HA-print](https://github.com/Glitchtit/HA-print)
- **Choice of AI**: Gemini (default), Claude, or a local Ollama model

## Installation

### Add-on (the app)

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add `https://github.com/Glitchtit/HA-apps`
2. Install **Storage** first, then **Recipe**
3. Set an AI provider and its key on the Configuration tab (the Gemini key can also come from Storage)
4. Open **Recipes** in the sidebar

### Integration (HACS)

Optional. Adds actions that automations, scripts and voice assistants can call.

1. In HACS, add this repository as a custom repository (category **Integration**)
2. Download **Recipes** and restart Home Assistant
3. **Settings → Devices & services → Add integration → Recipes**. The add-on is found automatically

| Action | What it does |
|--------|--------------|
| `ha_recipes.search` | Find saved recipes by name |
| `ha_recipes.scrape_url` | Import a recipe from a URL (takes up to a minute) |
| `ha_recipes.suggest_from_stock` | Recipes you can cook from what is in stock, expiring food first |

All three return a response, so they work with `response_variable` in scripts.

## Add-on options

| Option | Default | |
|--------|---------|---|
| `ai_provider` | `gemini` | `gemini`, `claude` or `ollama` |
| `gemini_api_key` / `gemini_model` | | Gemini key (falls back to the key saved in Storage) and model |
| `claude_api_key` / `claude_model` | | Anthropic API key and model |
| `ollama_url` / `ollama_model` | | Local Ollama server and model |
| `flaresolverr_url` | | e.g. `http://192.168.1.10:8191`, for Cloudflare-protected recipe sites |
| `debug` | `false` | Verbose logging |

## Development

```bash
# Backend + integration tests
python -m pytest -q

# Frontend dev server
cd recipes/frontend && npm install && npm run dev
```
