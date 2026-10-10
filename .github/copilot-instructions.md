# Copilot Instructions

## Project Overview

**Recipe** is a Home Assistant add-on for AI-powered recipe scraping. Users paste a recipe URL, Gemini AI extracts the recipe, and ingredients are matched to products in **HA-Storage**. The add-on slug is `recipes`.

## Architecture

Two s6-overlay services:

1. **recipes** (nginx on port 8099) — serves the React SPA and proxies API requests.
2. **recipe-backend** (Python on port 8100) — handles recipe scraping (summarize, then extract), hands every ingredient line to Storage for matching, and does Storage CRUD.

Request flow: **HA Ingress → nginx (port 8099) → React SPA / API proxies**.

nginx proxy routes:
- `/api/storage/*` → Storage API (HA-Storage addon)
- `/api/scraper/*` → Scraper addon (no longer used by the importer since 2.8.0)
- `/api/backend/*` → Python backend (localhost:8100)
- `/api/storage-files/*` → Storage file server (recipe images)

The Dockerfile is a **multi-stage build**: Node 20 builds the React frontend, then the HA base image runs nginx + Python.

## Config Options

```json
{
  "storage_url": "http://localhost:5000",
  "gemini_api_key": "str?",
  "gemini_model": "str?",
  "scraper_url": "url?",
  "flaresolverr_url": "str?",
  "debug": false
}
```

- `storage_url` — URL of the HA-Storage addon.
- `gemini_api_key` / `gemini_model` — optional local overrides. The AI key is fetched from Storage on startup (with retry); local config is fallback only.
- `scraper_url` — optional Scraper addon URL for product discovery.
- `flaresolverr_url` — optional FlareSolverr URL (e.g. `http://host:8191`). Recipe pages that answer with a Cloudflare challenge (k-ruoka.fi) are re-fetched through it; without it those scrapes fail with an actionable error.

## Development

Frontend commands run from `recipes/frontend/`:

```bash
npm install        # install dependencies
npm run dev        # dev server
npm run build      # production build to dist/
```

Tests: `cd recipes && python -m pytest tests -q` (backend) or `python -m pytest -q` from the submodule root (backend and the `ha_recipes` integration). No linter or formatter is configured.

## Key Conventions

- **Single-file React app**: All components live in `App.jsx`.
- **Dark mode**: Tailwind dark theme (`bg-gray-900`, `bg-gray-800`, emerald accents).
- **Ingress-aware URLs**: All API calls use `${INGRESS_PATH}/api/backend/...` or `/api/storage/...`. Never hard-code absolute paths.
- **API keys server-side**: Storage API key not needed (internal addon communication). Gemini key fetched from Storage.
- **Relative base path**: Vite is configured with `base: './'` for HA ingress compatibility.
- **Retry logic**: Backend retries connecting to Storage on startup. Frontend shows loading spinner until backend is ready.
- **Ingredient matching lives in Storage** (2.8.0): the importer sends each parsed line (`raw`, `name`, `specific`, `amount`, `unit_text`, `source_name`) to `POST /api/ingredients/resolve` and saves what comes back, with `raw_text` and `needs_review`. It never creates products, guesses units or writes conversions. `POST /api/recipe/<id>/reimport` re-fetches the source page and replaces the ingredients through Storage.
- **Recipe storage**: Recipes stored in Storage (SQLite). Instructions in the description field.
- **Multilingual**: Input can be Swedish/Finnish/English. All Storage products are in Finnish. The extract step translates ingredient names to Finnish; Storage remembers each source-language word as an alias of the concept it bound to.
- **Gemini AI**: Uses `google-genai` library (`from google import genai`). Model configurable via Storage config or addon options.

## HA Add-on Structure

- The add-on lives in `recipes/`, matching the slug in `config.json`.
- `config.json` defines add-on metadata, options schema, and ingress settings.
- `build.json` maps architectures to HA base images for multi-arch Docker builds.

## Versioning and Changelog

When making user-facing changes, **both files must be updated together**:

| File | Field |
|---|---|
| `recipes/config.json` | `"version": "X.Y.Z"` |
| `recipes/CHANGELOG.md` | New `## X.Y.Z` section |

CHANGELOG format: plain `## x.y.z` headers (no `v` prefix), flat bullet list, no dates, no category sub-headers. Newest version first.
