// Run with `npm test` (node's built-in test runner; no extra dependencies).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  fixPayload, formatQty, genericWord, haveText, recipeBadge, searchConcepts,
  shouldTeachAlias, skippedText, sortRecipes,
} from './recipeHelpers.js';

test('quantities use a decimal comma like Storage', () => {
  assert.equal(formatQty(3.5, 'dl'), '3,5 dl');
  assert.equal(formatQty(2, null), '2');
  assert.equal(formatQty(0.333, 'kg'), '0,33 kg');
});

test('badges: tick when everything is in stock, count of missing otherwise', () => {
  assert.deepEqual(recipeBadge({ ingredient_count: 4, missing_count: 0 }), { tone: 'ok', text: '✓' });
  assert.deepEqual(recipeBadge({ ingredient_count: 4, missing_count: 2 }), { tone: 'missing', text: '2 puuttuu' });
  assert.equal(recipeBadge({ ingredient_count: 0, missing_count: 0 }), null);
  assert.equal(recipeBadge({ ingredient_count: null, missing_count: null }), null);
});

test('cookable recipes sort first, then fewest missing, then by name', () => {
  const sorted = sortRecipes([
    { name: 'Öljy', ingredient_count: 2, missing_count: 1 },
    { name: 'Ahven', ingredient_count: 2, missing_count: 3 },
    { name: 'Pulla', ingredient_count: 5, missing_count: 0 },
    { name: 'Vanha', ingredient_count: null, missing_count: null },
    { name: 'Kakku', ingredient_count: 3, missing_count: 0 },
  ]);
  assert.deepEqual(sorted.map((r) => r.name), ['Kakku', 'Pulla', 'Öljy', 'Ahven', 'Vanha']);
});

test('a partly stocked ingredient says how much there is', () => {
  assert.equal(haveText({ status: 'yellow', amount_needed: 3, available: 1, unit_abbrev: 'dl' }), 'on 1 dl');
  assert.equal(haveText({ status: 'yellow', amount_needed: 300, available: 120.5, approx: true, unit_abbrev: 'g' }),
    'on ≈120,5 g');
  assert.equal(haveText({ status: 'green', amount_needed: 1, available: 4, unit_abbrev: 'kpl' }), '');
  assert.equal(haveText({ status: 'yellow', amount_needed: 1, available: 0, unit_abbrev: 'kpl' }), '');
});

test('the generic word is the last part of the note', () => {
  assert.equal(genericWord('hienonnettu — sipuli'), 'sipuli');
  assert.equal(genericWord('kerma'), 'kerma');
  assert.equal(genericWord(''), '');
});

test('concept search finds concepts only, names starting with the query first', () => {
  const products = [
    { id: 1, name: 'Ruokakerma', kind: 'concept' },
    { id: 2, name: 'Kerma', kind: 'concept' },
    { id: 3, name: 'Kermaviili Valio', kind: 'sku' },
    { id: 4, name: 'Kermajuusto', kind: 'concept' },
  ];
  assert.deepEqual(searchConcepts(products, 'kerma').map((p) => p.id), [2, 4, 1]);
  assert.deepEqual(searchConcepts(products, 'k'), []);
});

test('saving binds the concept loosely, or a chosen variant strictly', () => {
  assert.deepEqual(fixPayload({ conceptId: 5, variantId: null, amount: '2,5', unitId: 3 }),
    { product_id: 5, amount: 2.5, unit_id: 3, specificity: 'loose', needs_review: false });
  assert.deepEqual(fixPayload({ conceptId: 5, variantId: 9, amount: 1, unitId: 3 }),
    { product_id: 9, amount: 1, unit_id: 3, specificity: 'strict', needs_review: false });
});

test('an alias is taught only when the word differs from the concept name', () => {
  assert.equal(shouldTeachAlias('grädde', 'Kerma'), true);
  assert.equal(shouldTeachAlias('kerma', 'Kerma'), false);
  assert.equal(shouldTeachAlias('', 'Kerma'), false);
});

test('skipped import lines are named, three at most', () => {
  assert.equal(skippedText([]), '');
  assert.equal(skippedText(['1 st ???']), 'Ohitettiin 1 rivi, jota ei tunnistettu: "1 st ???"');
  assert.equal(skippedText(['a', 'b', 'c', 'd']),
    'Ohitettiin 4 riviä, joita ei tunnistettu: "a", "b", "c" ja 1 muu');
});
