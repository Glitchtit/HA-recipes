import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  fixPayload, genericWord, initialBinding, parseAmount, searchConcepts, shouldTeachAlias,
} from './recipeHelpers';

// Corrects one recipe ingredient when the import bound it wrong: which
// ingredient it is, any variant or exactly one, and the amount. When the
// ingredient changes, saving can also remember the recipe's word for it (an
// alias in Storage), so the next import and a re-import bind it right. That
// is opt-in: the word is the recipe's generic word ("kerma"), and remembering
// it re-binds that word in every future import.
export default function IngredientFixSheet({ apiStorage, recipeId, ingredient, onClose, onSaved, onToast }) {
  const [products, setProducts] = useState([]);
  const [units, setUnits] = useState([]);
  const [query, setQuery] = useState('');
  const [conceptId, setConceptId] = useState(null);
  const [startConceptId, setStartConceptId] = useState(null);
  const [variantId, setVariantId] = useState(null);
  const [exact, setExact] = useState(false);
  const [amount, setAmount] = useState(String(ingredient.amount_needed ?? '').replace('.', ','));
  const [unitId, setUnitId] = useState(ingredient.unit_id ?? null);
  const [teach, setTeach] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([axios.get(`${apiStorage}/products`), axios.get(`${apiStorage}/units`)])
      .then(([p, u]) => {
        if (cancelled) return;
        const list = Array.isArray(p.data) ? p.data : [];
        setProducts(list);
        setUnits(Array.isArray(u.data) ? u.data : []);
        const start = initialBinding(ingredient, list);
        setConceptId(start.conceptId);
        setStartConceptId(start.conceptId);
        setVariantId(start.variantId);
        setExact(start.exact);
      })
      .catch(() => onToast?.('Could not load products', 'error'));
    return () => { cancelled = true; };
  }, [apiStorage, ingredient, onToast]);

  const concept = products.find((p) => p.id === conceptId);
  const variants = useMemo(
    () => products.filter((p) => p.parent_id === conceptId)
      .sort((a, b) => a.name.localeCompare(b.name, 'fi')),
    [products, conceptId],
  );
  const hits = searchConcepts(products, query);
  const word = genericWord(ingredient.note);
  const canTeach = conceptId !== startConceptId && concept?.kind === 'concept'
    && shouldTeachAlias(word, concept.name);

  const parsedAmount = parseAmount(amount);
  const canSave = Boolean(conceptId) && unitId != null && parsedAmount !== null && !saving;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      await axios.put(`${apiStorage}/recipes/${recipeId}/ingredients/${ingredient.id}`,
        fixPayload({ conceptId, variantId, exact, amount, unitId }));
      if (canTeach && teach) {
        try {
          await axios.post(`${apiStorage}/products/${conceptId}/aliases`, { alias: word });
        } catch (err) {
          onToast?.(`Could not remember "${word}": ${err?.response?.data?.detail || 'error'}`, 'error');
        }
      }
      onToast?.('Ingredient fixed', 'success');
      onSaved?.();
    } catch (err) {
      onToast?.(`Save failed: ${err?.response?.data?.detail || err?.message || 'error'}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const field = 'rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-100 '
    + 'placeholder-gray-500 focus:outline-none focus:border-brand-cobalt';
  const label = 'text-xs font-bold text-gray-300 uppercase tracking-wide';
  const toggle = (on) => `flex-1 py-2 rounded-lg text-sm font-semibold transition-colors ${
    on ? 'bg-brand-cobalt text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 mb-4 sm:mb-0 p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-lg font-bold text-gray-100">Fix ingredient</h3>
            {ingredient.raw_text && (
              <p className="text-xs text-gray-400 mt-1">In recipe: {ingredient.raw_text}</p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-2xl leading-none px-2" aria-label="Close">
            ✕
          </button>
        </div>

        <div>
          <div className={label}>Ingredient</div>
          <div className="mt-1 text-sm font-medium text-gray-100">{concept ? concept.name : '…'}</div>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search for another ingredient…"
            className={`mt-2 w-full ${field}`}
          />
          {hits.length > 0 && (
            <ul className="mt-1 rounded-lg bg-gray-900 border border-gray-700 divide-y divide-gray-800 overflow-hidden">
              {hits.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => { setConceptId(p.id); setVariantId(null); setExact(false); setQuery(''); }}
                    className="w-full text-left px-3 py-2 text-sm text-gray-200 hover:bg-gray-700"
                  >
                    {p.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className={label}>Will any kind do?</div>
          <div className="mt-1 flex gap-2">
            <button onClick={() => { setExact(false); setVariantId(null); }} className={toggle(!exact)}>
              Any kind
            </button>
            <button onClick={() => setExact(true)} className={toggle(exact)}>
              Exactly this
            </button>
          </div>
          {exact && variants.length > 0 && (
            <select
              value={variantId ?? ''}
              onChange={(e) => setVariantId(e.target.value ? Number(e.target.value) : null)}
              className={`mt-2 w-full ${field}`}
            >
              <option value="">{concept ? concept.name : '…'}</option>
              {variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </div>

        <div>
          <div className={label}>Amount</div>
          <div className="mt-1 flex gap-2">
            <input
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className={`w-24 ${field}`}
            />
            <select
              value={unitId ?? ''}
              onChange={(e) => setUnitId(Number(e.target.value))}
              className={`flex-1 ${field}`}
            >
              {unitId == null && <option value="">Choose a unit</option>}
              {units.map((u) => <option key={u.id} value={u.id}>{u.abbreviation} – {u.name}</option>)}
            </select>
          </div>
          {parsedAmount === null ? (
            <p className="text-xs text-red-300 mt-1">Enter the amount as a number, e.g. 2,5</p>
          ) : (
            <p className="text-xs text-gray-500 mt-1">0 = to taste</p>
          )}
        </div>

        {canTeach && (
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={teach} onChange={(e) => setTeach(e.target.checked)} />
            Always read "{word}" as {concept.name} from now on
          </label>
        )}

        <button
          onClick={save}
          disabled={!canSave}
          className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
