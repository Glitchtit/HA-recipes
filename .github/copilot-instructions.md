# Copilot Instructions

## Project Overview

**Recipe** is a Home Assistant add-on for AI-powered recipe scraping. Users paste a recipe URL, an AI provider (Gemini by default; Claude or Ollama via `ai_provider`) extracts the recipe, and ingredients are matched to products in **HA-Storage**. The add-on slug is `recipes`. A second tab, **Setit**, manages named quick-add product sets (bundles) that are stored in Storage and pushed onto the shopping list.

The repo also ships a companion HACS integration, `ha_recipes` (`custom_components/ha_recipes/`), alongside the add-on.

## Architecture

Two s6-overlay services:

1. **recipes** (nginx on port 8099) — auto-detects the Storage, Scraper and HA-print add-ons (unless their URLs are set in options), renders the nginx config, serves the React SPA and proxies API requests. A background monitor re-detects auto-detected services every 30 s and reloads nginx when a URL changes.
2. **recipe-backend** (Python on port 8100) — handles recipe scraping (summarize, then extract), hands every ingredient line to Storage for matching, and does Storage CRUD.

Request flow: **HA Ingress → nginx (port 8099) → React SPA / API proxies**.

nginx proxy routes:
- `/api/storage/*` → Storage API (HA-Storage addon)
- `/api/scraper/*` → Scraper addon (no longer used by the importer since 2.8.0)
- `/api/backend/*` → Python backend (localhost:8100)
- `/api/storage-files/*` → Storage file server (recipe images)
- `/api/print/*` → HA-print add-on (port 8100), used by the 🖨 recipe print button

The Dockerfile is a **multi-stage build**: Node 20 builds the React frontend, then the HA base image runs nginx + Python.

## Config Options

`options` (defaults):

```json
{
  "ai_provider": "gemini",
  "gemini_api_key": "",
  "gemini_model": "gemini-2.0-flash",
  "ollama_url": "",
  "ollama_model": "llama3",
  "claude_api_key": "",
  "claude_model": "claude-3-5-haiku-20241022",
  "flaresolverr_url": "",
  "debug": false
}
```

`schema` additionally accepts `storage_url` (`url?`), `scraper_url` (`url?`) and `print_url` (`str?`); they have no default and are auto-detected when unset.

- `ai_provider` — `gemini` | `claude` | `ollama`. `/api/backend/config` reports `configured` only when Storage is set and the chosen provider has its key (or, for Ollama, its URL).
- `gemini_api_key` / `gemini_model`, `claude_api_key` / `claude_model`, `ollama_url` / `ollama_model` — provider credentials and models. The backend reads these only from its own add-on options (exported as env vars by the s6 run script); nothing is fetched from Storage.
- `storage_url` — optional HA-Storage URL; auto-detected via hostname / Supervisor API when unset.
- `scraper_url` — optional Scraper addon URL for the `/api/scraper/*` proxy (the importer no longer uses it since 2.8.0).
- `print_url` — optional HA-print URL for the `/api/print/*` proxy; auto-detected (port 8100) when unset.
- `flaresolverr_url` — optional FlareSolverr URL (e.g. `http://host:8191`). Recipe pages that answer with a Cloudflare challenge (k-ruoka.fi) are re-fetched through it; without it those scrapes fail with an actionable error.
- `debug` — verbose backend logging and full nginx request logging.

## Development

Frontend commands run from `recipes/frontend/`:

```bash
npm install        # install dependencies
npm run dev        # dev server
npm run build      # production build to dist/
npm test           # node:test unit tests for src/recipeHelpers.js
```

Tests: `cd recipes && python -m pytest tests -q` (backend) or `python -m pytest -q` from the submodule root (backend and the `ha_recipes` integration). No linter or formatter is configured.

## Key Conventions

- **Mostly single-file React app**: Most components live in `App.jsx`; the ingredient fix sheet is `IngredientFixSheet.jsx`, the changelog modal is `components/WhatsNewModal.jsx`, and pure helpers are in `recipeHelpers.js`.
- **Dark mode**: Tailwind dark theme (`bg-gray-900`, `bg-gray-800`) with GlitchyRee brand colours: `brand-cobalt` (#0047AB) for primary actions/active tabs and `brand-orange` (#FF4F00) for accents, defined in `src/styles/design-tokens.css` and mapped in `tailwind.config.js`. Emerald remains for success states (in-stock badges) and a few legacy buttons.
- **Ingress-aware URLs**: All API calls use `${INGRESS_PATH}/api/backend/...`, `/api/storage/...` or `/api/print/...`. Never hard-code absolute paths.
- **API keys server-side**: Storage API key not needed (internal addon communication). AI provider keys come only from this add-on's options.
- **Relative base path**: Vite is configured with `base: './'` for HA ingress compatibility.
- **Retry logic**: Backend retries connecting to Storage on startup. Frontend polls Storage health (`/api/storage/health`) with bounded retry and shows a loading spinner until it answers.
- **Ingredient matching lives in Storage** (2.8.0): the importer sends each parsed line (`raw`, `name`, `specific`, `amount`, `unit_text`, `source_name`) to `POST /api/ingredients/resolve` and saves what comes back, with `raw_text` and `needs_review`. It never creates products, guesses units or writes conversions. `POST /api/recipe/<id>/reimport` re-fetches the source page and replaces the ingredients through Storage.
- **Recipe storage**: Recipes stored in Storage (SQLite). Instructions in the description field.
- **Setit (bundles)**: The frontend calls Storage's `/api/bundles` CRUD and `/api/bundles/<id>/to-shopping` directly through `/api/storage/*`; the backend is not involved. Bundles are kept separate from recipes.
- **Multilingual**: Input can be Swedish/Finnish/English. All Storage products are in Finnish. The extract step translates ingredient names to Finnish; Storage remembers each source-language word as an alias of the concept it bound to.
- **AI providers**: `AI_PROVIDER` selects Gemini (`google-genai`, `from google import genai`), Claude (`anthropic`) or Ollama (HTTP). Models are configurable via addon options only.

## HA Add-on Structure

- The add-on lives in `recipes/`, matching the slug in `config.json`.
- `config.json` defines add-on metadata, options schema, and ingress settings.
- `build.json` maps architectures to HA base images for multi-arch Docker builds.

## HACS Integration (`ha_recipes`)

- Lives in `custom_components/ha_recipes/` at the repo root, parallel to `recipes/`; installed via HACS (`hacs.json`).
- Registers three response-only services (`SupportsResponse.ONLY`), no entities: `ha_recipes.search` (find saved recipes by name), `ha_recipes.scrape_url` (import a recipe from a URL), `ha_recipes.suggest_from_stock` (recipes cookable from current Storage stock, optional expiring-only filter).
- Talks to the add-on backend on port 8100 and to Storage on 8099, auto-discovered via the Supervisor API.
- Tests: `tests/` at the repo root.

## Versioning and Changelog

When making user-facing changes, **both add-on files must be updated together** (plus the integration manifest when the integration changes):

| File | Field |
|---|---|
| `recipes/config.json` | `"version": "X.Y.Z"` |
| `recipes/CHANGELOG.md` | New `## X.Y.Z` section |
| `custom_components/ha_recipes/manifest.json` | `"version"` (only when the integration changes; note it as "Integration X.Y.Z" in the changelog) |

CHANGELOG format: plain `## x.y.z` headers (no `v` prefix), flat bullet list, no dates, no category sub-headers. Newest version first.
