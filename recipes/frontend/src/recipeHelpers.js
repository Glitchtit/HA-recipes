// Pure helpers for the recipe list badges, ingredient quantities and the
// ingredient fix sheet. No React or browser APIs, so `npm test` (node's
// built-in test runner) covers them.

// 3.5 + 'dl' -> '3,5 dl'; the same format as Storage's display text.
export function formatQty(amount, unit) {
  let text = Number(amount || 0).toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  if (text === '' || text === '-0') text = '0';
  text = text.replace('.', ',');
  return unit ? `${text} ${unit}` : text;
}

// List badge, or null when Storage has not counted (before 0.27.0) or the
// recipe has no ingredients.
export function recipeBadge(recipe) {
  const missing = recipe?.missing_count;
  if (missing == null || !recipe.ingredient_count) return null;
  if (missing === 0) return { tone: 'ok', text: '✓' };
  return { tone: 'missing', text: `${missing} puuttuu` };
}

// Cookable recipes first, then the fewest missing ingredients, then by name.
export function sortRecipes(recipes) {
  const rank = (r) => (r.missing_count == null || !r.ingredient_count
    ? Number.MAX_SAFE_INTEGER : r.missing_count);
  return [...recipes].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'fi'));
}

// "on 1 dl" for an ingredient that is only partly in stock.
export function haveText(ing) {
  if (ing.status !== 'yellow' || !(ing.amount_needed > 0)) return '';
  if (typeof ing.available !== 'number' || ing.available <= 0) return '';
  return `on ${ing.approx ? '≈' : ''}${formatQty(ing.available, ing.unit_abbrev || 'kpl')}`;
}

// The word an import bound an ingredient by: the last part of its note
// ("hienonnettu — sipuli" -> "sipuli").
export function genericWord(note) {
  const parts = String(note || '').split(' — ');
  return parts[parts.length - 1].trim();
}

// Concepts whose name contains the query, names that start with it first.
export function searchConcepts(products, query, limit = 8) {
  const q = query.trim().toLocaleLowerCase('fi');
  if (q.length < 2) return [];
  const starts = (p) => (p.name.toLocaleLowerCase('fi').startsWith(q) ? 0 : 1);
  return products
    .filter((p) => p.kind === 'concept' && p.name.toLocaleLowerCase('fi').includes(q))
    .sort((a, b) => starts(a) - starts(b) || a.name.localeCompare(b.name, 'fi'))
    .slice(0, limit);
}

// The ingredient update the fix sheet saves: any variant of the concept
// (loose), or exactly the chosen variant (strict).
export function fixPayload({ conceptId, variantId, amount, unitId }) {
  return {
    product_id: variantId ?? conceptId,
    amount: Number(String(amount).replace(',', '.')),
    unit_id: unitId,
    specificity: variantId ? 'strict' : 'loose',
    needs_review: false,
  };
}

// Remembering the word as an alias only helps when it is not the name itself.
export function shouldTeachAlias(word, conceptName) {
  return Boolean(word) && word.toLocaleLowerCase('fi') !== String(conceptName || '').toLocaleLowerCase('fi');
}

// Toast text for import lines Storage could not bind.
export function skippedText(skipped) {
  if (!skipped?.length) return '';
  const n = skipped.length;
  const shown = skipped.slice(0, 3).map((s) => `"${s}"`).join(', ');
  const rest = n - 3;
  const more = rest > 0 ? ` ja ${rest} ${rest === 1 ? 'muu' : 'muuta'}` : '';
  return n === 1
    ? `Ohitettiin 1 rivi, jota ei tunnistettu: ${shown}`
    : `Ohitettiin ${n} riviä, joita ei tunnistettu: ${shown}${more}`;
}
