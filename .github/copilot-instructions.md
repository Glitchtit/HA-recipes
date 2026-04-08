# Copilot Instructions

## Project Overview

This is a **Home Assistant add-on** that provides an ingress-compatible AI-powered recipe scraper for [Grocy](https://grocy.info/). Users paste a recipe URL, Gemini AI extracts the recipe, and ingredients are matched to Grocy products. The add-on runs nginx + a Python backend, proxying requests to Grocy, the grocy-scraper addon, and the local backend.

## Architecture

Two s6-overlay services:

1. **grocy-recipes** (nginx on port 8099) — serves the React SPA and proxies API requests.
2. **recipe-backend** (Python on port 8100) — handles recipe scraping, product matching, and Grocy CRUD.

Request flow: **HA Ingress → nginx (port 8099) → React SPA / API proxies**.

nginx proxy locations:
- `/api/grocy/*` → Grocy instance (API key injected server-side)
- `/api/scraper/*` → grocy-scraper addon (auto-detected)
- `/api/backend/*` → Python backend (localhost:8100)
- `/api/grocy-files/*` → Grocy file server (for recipe images)

The Dockerfile is a **multi-stage build**: Node 20 builds the React frontend, then the HA base image runs nginx + Python.

## Development Commands

Frontend commands run from `grocy_recipes/frontend/`:

```bash
npm install        # install dependencies
npm run dev        # dev server at localhost:5173
npm run build      # production build to dist/
```

There is no test suite, linter, or formatter configured.

## Key Conventions

- **Single-file React app**: All components (App, RecipeCard, RecipeDetail, ShoppingListDialog, etc.) live in `App.jsx`.
- **Ingress-aware URLs**: All API calls use `${INGRESS_PATH}/api/backend/...`. Never hard-code absolute paths.
- **API key is server-side only**: The Grocy API key is added by nginx, not the frontend.
- **Relative base path**: Vite is configured with `base: './'` for HA ingress compatibility.
- **Finnish UI**: All user-facing strings are in Finnish.
- **Gemini AI**: Uses `google-genai` library (`from google import genai`). Model configurable via addon options.
- **Product matching strategy**: Exact match → substring match (prefer parents) → AI match → scraper discovery.
- **Recipe storage**: Recipes are stored in Grocy's built-in recipe system. Instructions stored in the description field.

## HA Add-on Structure

- `repository.json` at the repo root registers this as an HA add-on repository.
- The add-on lives in `grocy_recipes/`, matching the `slug` in `config.json`.
- `config.json` defines add-on metadata, options schema (`grocy_base_url`, `grocy_api_key`, `gemini_api_key`, `gemini_model`), and ingress settings.
- `build.json` maps architectures to HA base images for multi-arch Docker builds.

## Versioning and Changelog

When making changes that warrant a release, **both files must be updated together**:

1. **`grocy_recipes/config.json`** — bump the `"version"` field following [Semantic Versioning](https://semver.org/):
   - **MAJOR** (e.g. 1.0.0 → 2.0.0): breaking changes or major rework.
   - **MINOR** (e.g. 1.0.0 → 1.1.0): new features, backwards-compatible.
   - **PATCH** (e.g. 1.0.0 → 1.0.1): bug fixes, dependency bumps, minor tweaks.

2. **`grocy_recipes/CHANGELOG.md`** — add a new section **at the top** of the file, below the `# Changelog` heading. Follow the official Home Assistant add-on changelog format (flat bullet list per version, no date stamps, no category headers):

   ```markdown
   ## 1.1.0

   - Add search bar to filter recipes
   - Fix image rendering on slow connections
   ```

   **Format rules (match official HA add-ons):**
   - Use `## x.y.z` as the version heading (no `v` prefix).
   - Each change is a single `- ` bullet — concise, user-facing language.
   - Newest version goes first, above all previous entries.
   - No date stamps, no "Added/Changed/Fixed" category sub-headers.

The version in `config.json` is what Home Assistant displays to users and uses to detect updates. The `CHANGELOG.md` is shown in the add-on details page. **These must always stay in sync.**
