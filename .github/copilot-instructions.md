# Copilot Instructions

## Project Overview

**Recipe** is a Home Assistant add-on for AI-powered recipe scraping. Users paste a recipe URL, Gemini AI extracts the recipe, and ingredients are matched to products in **HA-Storage** (the Storage addon, NOT Grocy). The add-on slug is `grocy_recipes` (legacy name).

## Architecture

Two s6-overlay services:

1. **grocy-recipes** (nginx on port 8099) — serves the React SPA and proxies API requests.
2. **recipe-backend** (Python on port 8100) — handles recipe scraping, product matching, and Storage CRUD.

Request flow: **HA Ingress → nginx (port 8099) → React SPA / API proxies**.

nginx proxy routes:
- `/api/storage/*` → Storage API (HA-Storage addon)
- `/api/scraper/*` → Scraper addon (for product discovery)
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
  "debug": false
}
```

- `storage_url` — URL of the HA-Storage addon.
- `gemini_api_key` / `gemini_model` — optional local overrides. The AI key is fetched from Storage on startup (with retry); local config is fallback only.
- `scraper_url` — optional Scraper addon URL for product discovery.

## Development

Frontend commands run from `grocy_recipes/frontend/`:

```bash
npm install        # install dependencies
npm run dev        # dev server
npm run build      # production build to dist/
```

There is no test suite, linter, or formatter configured.

## Key Conventions

- **Single-file React app**: All components live in `App.jsx`.
- **Dark mode**: Tailwind dark theme (`bg-gray-900`, `bg-gray-800`, emerald accents).
- **Ingress-aware URLs**: All API calls use `${INGRESS_PATH}/api/backend/...` or `/api/storage/...`. Never hard-code absolute paths.
- **API keys server-side**: Storage API key not needed (internal addon communication). Gemini key fetched from Storage.
- **Relative base path**: Vite is configured with `base: './'` for HA ingress compatibility.
- **Retry logic**: Backend retries connecting to Storage on startup. Frontend shows loading spinner until backend is ready.
- **Product matching strategy**: Exact match → substring match (prefer parents) → AI match → scraper discovery.
- **Recipe storage**: Recipes stored in Storage (SQLite). Instructions in the description field.
- **Multilingual**: Input can be Swedish/Finnish/English. All Storage products are in Finnish. AI translates ingredient names to Finnish for matching.
- **Gemini AI**: Uses `google-genai` library (`from google import genai`). Model configurable via Storage config or addon options.

## HA Add-on Structure

- The add-on lives in `grocy_recipes/`, matching the slug in `config.json`.
- `config.json` defines add-on metadata, options schema, and ingress settings.
- `build.json` maps architectures to HA base images for multi-arch Docker builds.

## Versioning and Changelog

When making user-facing changes, **both files must be updated together**:

| File | Field |
|---|---|
| `grocy_recipes/config.json` | `"version": "X.Y.Z"` |
| `grocy_recipes/CHANGELOG.md` | New `## X.Y.Z` section |

CHANGELOG format: plain `## x.y.z` headers (no `v` prefix), flat bullet list, no dates, no category sub-headers. Newest version first.
