import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { fixPayload, genericWord, searchConcepts, shouldTeachAlias } from './recipeHelpers';

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
        // A strict ingredient is bound to a variant; its ingredient is the parent.
        const bound = list.find((x) => x.id === ingredient.product_id);
        const start = ingredient.specificity === 'strict' && bound?.parent_id
          ? bound.parent_id : ingredient.product_id;
        setConceptId(start);
        setStartConceptId(start);
        if (start !== ingredient.product_id) setVariantId(bound.id);
      })
      .catch(() => onToast?.('Tuotteiden lataus epäonnistui', 'error'));
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

  const save = async () => {
    if (!conceptId || unitId == null || saving) return;
    setSaving(true);
    try {
      await axios.put(`${apiStorage}/recipes/${recipeId}/ingredients/${ingredient.id}`,
        fixPayload({ conceptId, variantId, amount, unitId }));
      if (canTeach && teach) {
        try {
          await axios.post(`${apiStorage}/products/${conceptId}/aliases`, { alias: word });
        } catch (err) {
          onToast?.(`Sanaa "${word}" ei voitu muistaa: ${err?.response?.data?.detail || 'virhe'}`, 'error');
        }
      }
      onToast?.('Ainesosa korjattu', 'success');
      onSaved?.();
    } catch (err) {
      onToast?.(`Tallennus epäonnistui: ${err?.response?.data?.detail || err?.message || 'virhe'}`, 'error');
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
            <h3 className="text-lg font-bold text-gray-100">Korjaa ainesosa</h3>
            {ingredient.raw_text && (
              <p className="text-xs text-gray-400 mt-1">Reseptissä: {ingredient.raw_text}</p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-2xl leading-none px-2" aria-label="Sulje">
            ✕
          </button>
        </div>

        <div>
          <div className={label}>Ainesosa</div>
          <div className="mt-1 text-sm font-medium text-gray-100">{concept ? concept.name : '…'}</div>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Hae toinen ainesosa…"
            className={`mt-2 w-full ${field}`}
          />
          {hits.length > 0 && (
            <ul className="mt-1 rounded-lg bg-gray-900 border border-gray-700 divide-y divide-gray-800 overflow-hidden">
              {hits.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => { setConceptId(p.id); setVariantId(null); setQuery(''); }}
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
          <div className={label}>Kelpaako mikä tahansa?</div>
          <div className="mt-1 flex gap-2">
            <button onClick={() => setVariantId(null)} className={toggle(variantId == null)}>
              Mikä tahansa
            </button>
            <button
              onClick={() => variants.length && setVariantId(variantId ?? variants[0].id)}
              disabled={!variants.length}
              className={`${toggle(variantId != null)} disabled:opacity-40`}
            >
              Juuri tämä
            </button>
          </div>
          {variantId != null && (
            <select
              value={variantId}
              onChange={(e) => setVariantId(Number(e.target.value))}
              className={`mt-2 w-full ${field}`}
            >
              {variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
        </div>

        <div>
          <div className={label}>Määrä</div>
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
              {unitId == null && <option value="">Valitse yksikkö</option>}
              {units.map((u) => <option key={u.id} value={u.id}>{u.abbreviation} – {u.name}</option>)}
            </select>
          </div>
          <p className="text-xs text-gray-500 mt-1">0 = maun mukaan</p>
        </div>

        {canTeach && (
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={teach} onChange={(e) => setTeach(e.target.checked)} />
            Muista jatkossa: "{word}" on aina {concept.name}
          </label>
        )}

        <button
          onClick={save}
          disabled={!conceptId || unitId == null || saving}
          className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Tallennetaan…' : 'Tallenna'}
        </button>
      </div>
    </div>
  );
}
