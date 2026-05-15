# Lessons

## Two AI prompt paths in backend.py — verify which is live before editing (2026-05-16)

`backend.py` contains two distinct ingredient-translation functions with overlapping but **inconsistent** prompts:

| Function | Lines (approx.) | Used by recipe scraping? |
|---|---|---|
| `_translate_ingredients` | 1197 | **No** — has no callers in `backend.py` |
| `_summarize_recipe` → `_extract_recipe_from_summary` | 1303 / 1337 | **Yes** — called from the scrape entry point at `scrape_recipe()` ~line 1442 |

When fixing how the recipe scraper handles ingredient variants, the edits must go into the **summarize+extract** pair, not `_translate_ingredients`. The 2.2.1 release fixed `_translate_ingredients` and shipped with zero effect on real imports — diagnosed only after the user re-imported and saw the same collapsed result.

**How to verify which prompt is live:** before editing any prompt in `backend.py`, run:

```bash
grep -nE "def _translate_ingredients|def _summarize_recipe|def _extract_recipe_from_summary" recipes/backend.py
grep -nE "_translate_ingredients|_extract_recipe_from_summary" recipes/backend.py | grep -v "^.*def "
```

The second `grep` shows call sites. A function with no call sites is dead on the live path.

**Why two paths exist:** `_translate_ingredients` was the older single-shot translator. The summarize+extract pair was added later to give the AI a cleaner intermediate representation (parenthetical weights stripped, "to taste" marked) before final JSON extraction. The older function was left in place for ad-hoc use but is currently unreferenced.

**Until both paths are unified, any prompt rule change must be applied to both** if you want it to apply uniformly to any future caller. The 2.2.1+2.2.2 changes touch both for that reason.
