import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import WhatsNewModal from './components/WhatsNewModal';
import IngredientFixSheet from './IngredientFixSheet';
import { formatQty, haveText, recipeBadge, servingsText, skippedText, sortRecipes } from './recipeHelpers';

// ---------------------------------------------------------------------------
// Ingress-path awareness
// ---------------------------------------------------------------------------
const INGRESS_PATH =
  document.querySelector('meta[name="ingress-path"]')?.content ?? '';

const API_BACKEND = `${INGRESS_PATH}/api/backend`;
const API_STORAGE = `${INGRESS_PATH}/api/storage`;
const API_PRINT = `${INGRESS_PATH}/api/print`;

// Fetch an image URL and return raw base64 (no data: prefix).
async function fetchImageAsBase64(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Image fetch failed (${r.status})`);
  const blob = await r.blob();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || '');
      const i = s.indexOf(',');
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function recipeImageUrl(filename) {
  if (!filename) return null;
  return `${INGRESS_PATH}/api/storage-files/recipes/${encodeURIComponent(filename)}`;
}

// ---------------------------------------------------------------------------
// Toast component
// ---------------------------------------------------------------------------
function Toasts({ toasts }) {
  return (
    <div className="fixed bottom-4 right-4 z-[80] space-y-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`px-4 py-3 rounded-xl text-sm font-medium shadow-lg text-white ${
            t.type === 'error'
              ? 'bg-red-600'
              : t.type === 'success'
                ? 'bg-emerald-600'
                : 'bg-gray-700'
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RecipeCard — grid item for recipe list
// ---------------------------------------------------------------------------
function RecipeCard({ recipe, onClick }) {
  const imgUrl = recipeImageUrl(recipe.picture_filename);
  const badge = recipeBadge(recipe);
  return (
    <button
      onClick={onClick}
      className="bg-gray-800 rounded-2xl shadow-lg overflow-hidden text-left hover:ring-2 hover:ring-brand-orange transition-all active:scale-[0.98]"
    >
      <div className="aspect-video bg-gray-700 relative overflow-hidden">
        {imgUrl ? (
          <img
            src={imgUrl}
            alt={recipe.name}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex items-center justify-center h-full text-gray-500 text-4xl">
            🍽️
          </div>
        )}
        {badge && (
          <span
            className={`absolute top-2 right-2 px-2 py-0.5 rounded-full text-xs font-semibold ${
              badge.tone === 'ok' ? 'bg-emerald-900/80 text-emerald-300' : 'bg-red-900/80 text-red-300'
            }`}
          >
            {badge.text}
          </span>
        )}
      </div>
      <div className="p-3">
        <h3 className="font-semibold text-gray-100 text-sm line-clamp-2">
          {recipe.name}
        </h3>
        <p className="text-gray-400 text-xs mt-1">
          {servingsText(recipe.servings)}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// RecipeDetail — full-screen overlay
// ---------------------------------------------------------------------------
function RecipeDetail({ recipe, onClose, onAddToShoppingList, onCook, onDelete, onReimport, reimporting, onToast, onFixed }) {
  // Every hook runs before the early return below (rules of hooks).
  const [fixing, setFixing] = useState(null);
  const [printing, setPrinting] = useState(false);
  const imgUrl = recipe ? recipeImageUrl(recipe.picture_filename) : null;
  const handlePrint = useCallback(async () => {
    if (printing) return;
    setPrinting(true);
    try {
      let image_b64 = null;
      if (imgUrl) {
        try {
          image_b64 = await fetchImageAsBase64(imgUrl);
        } catch (e) {
          // Non-fatal: print without the hero image.
          // eslint-disable-next-line no-console
          console.warn('Could not fetch recipe image:', e);
        }
      }
      const ingredients = (recipe.ingredients || []).map((ing) => ({
        product_name: ing.product_name,
        amount_needed: ing.amount_needed,
        unit_abbrev: ing.unit_abbrev,
        parent_name: ing.parent_name,
        note: ing.note,
      }));
      const payload = {
        recipe: {
          name: recipe.name,
          servings: recipe.servings,
          source_url: recipe.source_url,
          ingredients,
          instructions: recipe.instructions || [],
        },
        image_b64,
      };
      await axios.post(`${API_PRINT}/print/recipe`, payload);
      onToast?.('Printed', 'success');
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'unknown error';
      onToast?.(`Print failed: ${msg}`, 'error');
    } finally {
      setPrinting(false);
    }
  }, [recipe, imgUrl, printing, onToast]);

  if (!recipe) return null;
  const hasOpened = recipe.ingredients?.some(
    (i) => i.status === 'yellow',
  );

  return (
    <>
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm overlay-enter overflow-y-auto py-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden overlay-card-enter"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <div className="flex justify-end p-2">
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-2xl leading-none px-2"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Recipe image */}
        {imgUrl && (
          <div className="px-4 pb-4">
            <img
              src={imgUrl}
              alt={recipe.name}
              className="w-full rounded-xl object-cover max-h-64"
            />
          </div>
        )}

        {/* Recipe info */}
        <div className="px-6 pb-4">
          <h2 className="text-xl font-bold text-gray-100">{recipe.name}</h2>
          <p className="text-gray-400 mt-1 text-sm">
            {servingsText(recipe.servings)}
            {recipe.source_url && (
              <>
                {' · '}
                <a
                  href={recipe.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-emerald-400 hover:underline"
                >
                  Source
                </a>
              </>
            )}
          </p>
        </div>

        {/* Ingredients */}
        <div className="px-6 pb-4">
          <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wide mb-2">
            Ingredients
          </h3>
          <ul className="space-y-1">
            {recipe.ingredients?.map((ing) => (
              <li key={ing.id}>
                <button
                  type="button"
                  onClick={() => setFixing(ing)}
                  title={ing.raw_text || 'Fix ingredient'}
                  className={`w-full text-left text-sm px-3 py-2 rounded-lg ${
                    ing.status === 'green'
                      ? 'bg-brand-orange text-white'
                      : ing.status === 'yellow'
                        ? 'bg-amber-900/40 text-amber-300'
                        : 'bg-red-900/40 text-red-300'
                  }`}
                >
                  <span className="font-medium">{ing.product_name}</span>
                  {ing.amount_needed > 0 ? (
                    <span className="text-xs opacity-70 ml-2">
                      — {formatQty(ing.amount_needed, ing.unit_abbrev || 'kpl')}
                    </span>
                  ) : (
                    <span className="text-xs opacity-70 ml-2">— to taste</span>
                  )}
                  {haveText(ing) && (
                    <span className="text-xs opacity-70 ml-2">· {haveText(ing)}</span>
                  )}
                  {ing.needs_review && (
                    <span className="ml-2" title="Unit not recognized, check the amount">⚠</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* Instructions */}
        {recipe.instructions?.length > 0 && (
          <div className="px-6 pb-4">
            <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wide mb-2">
              Steps
            </h3>
            <ol className="list-decimal pl-5 space-y-3 text-gray-300 text-sm">
              {recipe.instructions.map((step, i) => (
                <li key={i} className="leading-relaxed pl-1" dangerouslySetInnerHTML={{ __html: step }} />
              ))}
            </ol>
          </div>
        )}

        {/* Action buttons */}
        <div className="px-6 pb-6 space-y-2">
          <button
            onClick={() => onCook?.()}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 transition-colors"
          >
            🍳 Cook (deduct from stock)
          </button>
          <button
            onClick={() => onAddToShoppingList(hasOpened)}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors"
          >
            Add to shopping list
          </button>
          <button
            onClick={handlePrint}
            disabled={printing}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 disabled:hover:bg-gray-700 transition-colors"
          >
            {printing ? '🖨 Printing…' : '🖨 Print receipt'}
          </button>
          {recipe.source_url && (
            <button
              onClick={() => onReimport?.()}
              disabled={reimporting}
              className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 disabled:hover:bg-gray-700 transition-colors"
            >
              {reimporting ? '↻ Updating…' : '↻ Update ingredients from source'}
            </button>
          )}
          <button
            onClick={onDelete}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Remove recipe
          </button>
        </div>
      </div>
    </div>
    {fixing && (
      <IngredientFixSheet
        apiStorage={API_STORAGE}
        recipeId={recipe.id}
        ingredient={fixing}
        onClose={() => setFixing(null)}
        onSaved={() => { setFixing(null); onFixed?.(); }}
        onToast={onToast}
      />
    )}
    </>
  );
}

// ---------------------------------------------------------------------------
// ShoppingListDialog
// ---------------------------------------------------------------------------
function ShoppingListDialog({ hasOpened, onSelect, onClose }) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm overlay-enter"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-xs mx-4 p-6 overlay-card-enter"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100 text-center mb-2">
          Add to shopping list
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          Which ingredients?
        </p>
        <div className="space-y-2">
          <button
            onClick={() => onSelect('missing')}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Only missing
          </button>
          {hasOpened && (
            <button
              onClick={() => onSelect('missing_and_opened')}
              className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-amber-500 hover:bg-amber-600 active:bg-amber-700 transition-colors"
            >
              Missing + opened
            </button>
          )}
          <button
            onClick={() => onSelect('all')}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors"
          >
            All ingredients
          </button>
          <button
            onClick={onClose}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CookDialog
// Asks the user how many servings to cook, then fires /recipes/:id/cook.
// ---------------------------------------------------------------------------
function CookDialog({ recipe, onConfirm, onClose, busy }) {
  const defaultServings = recipe?.servings || 4;
  const [servings, setServings] = useState(String(defaultServings));

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm overlay-enter"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-xs mx-4 p-6 overlay-card-enter"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100 text-center mb-1">
          Cook
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          Deduct the ingredients from stock — missing ones go on the shopping list.
        </p>
        <label className="block text-sm text-gray-300 mb-1">Servings</label>
        <input
          type="number"
          value={servings}
          onChange={(e) => setServings(e.target.value)}
          min="0.5"
          step="0.5"
          className="w-full px-3 py-2 mb-4 bg-gray-900 border border-gray-700 rounded-lg text-white"
        />
        <div className="space-y-2">
          <button
            onClick={() => onConfirm(Number(servings) || defaultServings)}
            disabled={busy}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 disabled:bg-gray-700 transition-colors"
          >
            {busy ? 'Deducting…' : 'Confirm'}
          </button>
          <button
            onClick={onClose}
            disabled={busy}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeleteConfirmDialog
// ---------------------------------------------------------------------------
function DeleteConfirmDialog({
  recipeName,
  onConfirm,
  onClose,
  title = 'Remove recipe?',
  question = 'Remove the recipe',
}) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm overlay-enter"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-xs mx-4 p-6 overlay-card-enter"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100 text-center mb-2">
          {title}
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          {question}{' '}
          <span className="font-semibold text-gray-200">"{recipeName}"</span>?
        </p>
        <div className="space-y-2">
          <button
            onClick={onConfirm}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Remove
          </button>
          <button
            onClick={onClose}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BundleCard — grid item for the Sets tab
// ---------------------------------------------------------------------------
function BundleCard({ bundle, onClick, onEdit }) {
  return (
    <div className="bg-gray-800 rounded-2xl shadow-lg overflow-hidden relative">
      <button
        onClick={onClick}
        className="w-full text-left p-4 hover:bg-gray-700 transition-colors active:scale-[0.98]"
      >
        <div className="text-4xl mb-2">{bundle.emoji || '🧺'}</div>
        <h3 className="font-semibold text-gray-100 text-sm line-clamp-2">{bundle.name}</h3>
        <p className="text-gray-400 text-xs mt-1">
          {bundle.item_count} product{bundle.item_count !== 1 ? 's' : ''}
        </p>
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onEdit(); }}
        className="absolute top-2 right-2 w-8 h-8 rounded-lg text-gray-400 hover:text-gray-100 hover:bg-gray-700 text-sm"
        aria-label="Edit"
        title="Edit"
      >
        ✎
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BundlePickerOverlay — checklist push-to-shopping-list
// Items already in stock or already on the list start unchecked.
// ---------------------------------------------------------------------------
function BundlePickerOverlay({ bundle, onConfirm, onClose, busy }) {
  const [unchecked, setUnchecked] = useState(() => new Set(
    (bundle.items || [])
      .filter((i) => i.stock_amount > 0 || i.on_list)
      .map((i) => i.product_id),
  ));
  const toggle = (pid) => {
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };
  const checkedIds = (bundle.items || [])
    .map((i) => i.product_id)
    .filter((pid) => !unchecked.has(pid));

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm overlay-enter"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4 p-6 overlay-card-enter max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100 text-center mb-1">
          {bundle.emoji || '🧺'} {bundle.name}
        </h3>
        <p className="text-gray-400 text-sm text-center mb-4">
          Choose which products to add to the shopping list.
        </p>
        <ul className="space-y-1 mb-5">
          {(bundle.items || []).map((item) => {
            const checked = !unchecked.has(item.product_id);
            const hint = item.on_list ? 'on the list'
              : item.stock_amount > 0 ? 'in stock' : '';
            return (
              <li key={item.id}>
                <button
                  onClick={() => toggle(item.product_id)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left text-sm transition-colors ${
                    checked ? 'bg-gray-700 text-gray-100' : 'bg-gray-900 text-gray-500'
                  }`}
                >
                  <span
                    className={`w-5 h-5 flex-shrink-0 rounded border-2 flex items-center justify-center text-[11px] ${
                      checked
                        ? 'bg-brand-cobalt border-brand-cobalt text-white'
                        : 'border-gray-600 text-transparent'
                    }`}
                  >
                    ✓
                  </span>
                  <span className="flex-1 truncate">{item.product_name}</span>
                  {hint && <span className="text-[10px] uppercase tracking-wide text-amber-400/80">{hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="space-y-2">
          <button
            onClick={() => onConfirm(checkedIds)}
            disabled={busy || checkedIds.length === 0}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 disabled:opacity-40 transition-colors"
          >
            {busy ? 'Adding…' : `Add to shopping list (${checkedIds.length})`}
          </button>
          <button
            onClick={onClose}
            disabled={busy}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BundleEditorOverlay — create / edit a bundle with a product search picker
// ---------------------------------------------------------------------------
function BundleEditorOverlay({ bundle, products, onSave, onDelete, onClose, busy }) {
  const isNew = !bundle?.id;
  const [name, setName] = useState(bundle?.name || '');
  const [emoji, setEmoji] = useState(bundle?.emoji || '🧺');
  const [items, setItems] = useState(() =>
    (bundle?.items || []).map((i) => ({ product_id: i.product_id, product_name: i.product_name })),
  );
  const [query, setQuery] = useState('');

  const itemIds = new Set(items.map((i) => i.product_id));
  const q = query.trim().toLowerCase();
  const matches = q.length < 2 ? [] : (products || [])
    .filter((p) => (p.active ?? true) && !itemIds.has(p.id) && p.name.toLowerCase().includes(q))
    .slice(0, 8);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm overlay-enter"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4 p-6 overlay-card-enter max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-gray-100 text-center mb-4">
          {isNew ? 'New set' : 'Edit set'}
        </h3>
        <div className="flex gap-2 mb-3">
          <input
            type="text"
            value={emoji}
            onChange={(e) => setEmoji(e.target.value)}
            className="w-16 px-2 py-2 bg-gray-900 border border-gray-700 rounded-lg text-center text-xl"
            aria-label="Emoji"
          />
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Set name, e.g. Taco night"
            className="flex-1 px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-brand-cobalt"
          />
        </div>

        {/* Chosen products */}
        {items.length > 0 && (
          <ul className="space-y-1 mb-3">
            {items.map((item) => (
              <li
                key={item.product_id}
                className="flex items-center gap-2 px-3 py-2 bg-gray-700 rounded-lg text-sm text-gray-100"
              >
                <span className="flex-1 truncate">{item.product_name}</span>
                <button
                  onClick={() => setItems((prev) => prev.filter((x) => x.product_id !== item.product_id))}
                  className="text-gray-400 hover:text-red-400"
                  aria-label={`Remove ${item.product_name}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Product search */}
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search products…"
          className="w-full px-3 py-2 mb-1 bg-gray-900 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-brand-cobalt"
        />
        {matches.length > 0 && (
          <ul className="mb-3 rounded-lg overflow-hidden border border-gray-700 divide-y divide-gray-700">
            {matches.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => {
                    setItems((prev) => [...prev, { product_id: p.id, product_name: p.name }]);
                    setQuery('');
                  }}
                  className="w-full px-3 py-2 bg-gray-900 hover:bg-gray-700 text-left text-sm text-gray-200"
                >
                  ＋ {p.name}
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="space-y-2 mt-4">
          <button
            onClick={() => onSave({ name: name.trim(), emoji: emoji.trim() || '🧺', items: items.map((i) => ({ product_id: i.product_id })) })}
            disabled={busy || !name.trim()}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 disabled:opacity-40 transition-colors"
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
          {!isNew && (
            <button
              onClick={onDelete}
              disabled={busy}
              className="w-full py-2 rounded-xl font-semibold text-red-400 text-sm hover:text-red-300 transition-colors"
            >
              Remove set
            </button>
          )}
          <button
            onClick={onClose}
            disabled={busy}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [storageReady, setStorageReady] = useState(false);
  const [storageChecking, setStorageChecking] = useState(true);
  const [healthRetries, setHealthRetries] = useState(0);
  const MAX_HEALTH_RETRIES = 60;
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scraping, setScraping] = useState(false);
  const [reimporting, setReimporting] = useState(false);
  const [url, setUrl] = useState('');
  const [toasts, setToasts] = useState([]);

  // Tabs: 'recipes' | 'bundles'
  const [tab, setTab] = useState('recipes');

  // Bundles (Sets)
  const [bundles, setBundles] = useState([]);
  const [bundlesLoading, setBundlesLoading] = useState(false);
  const [allProducts, setAllProducts] = useState([]);
  const [pickerBundle, setPickerBundle] = useState(null);   // BundleDetail
  const [pickerBusy, setPickerBusy] = useState(false);
  const [editorBundle, setEditorBundle] = useState(null);   // BundleDetail | {} for new
  const [editorBusy, setEditorBusy] = useState(false);
  const [bundleDeleteDialog, setBundleDeleteDialog] = useState(null); // {id, name}

  // Detail view
  const [selectedRecipeId, setSelectedRecipeId] = useState(null);
  const [recipeDetail, setRecipeDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Connection status
  const [disconnected, setDisconnected] = useState(false);

  // Dialogs
  const [shoppingDialog, setShoppingDialog] = useState(null); // {hasOpened: bool}
  const [cookDialog, setCookDialog] = useState(null); // recipe object when open
  const [cookBusy, setCookBusy] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(null); // {id, name}

  // ── Toast helper ──────────────────────────────────────────────────
  const addToast = useCallback((message, type = 'error') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(
      () => setToasts((prev) => prev.filter((t) => t.id !== id)),
      5000,
    );
  }, []);

  // ── Storage health check ──────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let timer;
    let retryCount = 0;
    const check = async () => {
      try {
        const { data } = await axios.get(`${API_STORAGE}/health`, { timeout: 5000 });
        if (!cancelled && data && data.version) {
          setStorageReady(true);
          setStorageChecking(false);
          return;
        }
      } catch { /* retry */ }
      retryCount++;
      if (!cancelled) {
        setHealthRetries(retryCount);
        if (retryCount < MAX_HEALTH_RETRIES) {
          timer = setTimeout(check, 5000);
        } else {
          setStorageChecking(false);
        }
      }
    };
    check();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  // ── Load recipes ──────────────────────────────────────────────────
  const loadRecipes = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BACKEND}/recipes`);
      if (data.success) {
        setRecipes(data.recipes);
      }
    } catch (err) {
      addToast('Could not load recipes', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (storageReady) loadRecipes();
  }, [storageReady, loadRecipes]);

  // ── Heartbeat keep-alive (prevents Cloudflare 524 timeout) ───────
  useEffect(() => {
    let timer;
    const ping = async () => {
      try {
        const resp = await axios.get(`${API_BACKEND}/config`, { timeout: 10000 });
        if (resp.data && typeof resp.data === 'object') {
          setDisconnected(false);
        } else {
          setDisconnected(true);
        }
      } catch {
        setDisconnected(true);
      }
      timer = setTimeout(ping, 45000);
    };
    timer = setTimeout(ping, 45000);

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        clearTimeout(timer);
        ping();
      }
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  // ── Scrape recipe ─────────────────────────────────────────────────
  const handleScrape = useCallback(async () => {
    if (!url.trim() || scraping) return;
    setScraping(true);
    try {
      const { data } = await axios.post(`${API_BACKEND}/recipe/scrape`, {
        url: url.trim(),
      });
      if (data.success) {
        addToast(`Recipe "${data.name}" saved`, 'success');
        if (data.skipped?.length) addToast(skippedText(data.skipped), 'info');
        setUrl('');
        loadRecipes();
      } else {
        addToast(data.error || 'Could not import the recipe', 'error');
      }
    } catch (err) {
      const msg =
        err?.response?.data?.error || 'Could not import the recipe';
      addToast(msg, 'error');
    } finally {
      setScraping(false);
    }
  }, [url, scraping, addToast, loadRecipes]);

  // ── Load recipe detail ────────────────────────────────────────────
  const openRecipe = useCallback(
    async (id) => {
      setSelectedRecipeId(id);
      setDetailLoading(true);
      try {
        const { data } = await axios.get(`${API_BACKEND}/recipe/${id}`);
        if (data.success) {
          setRecipeDetail(data.recipe);
        } else {
          addToast('Could not load the recipe', 'error');
          setSelectedRecipeId(null);
        }
      } catch {
        addToast('Could not load the recipe', 'error');
        setSelectedRecipeId(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [addToast],
  );

  // Reload the open recipe in place (after an ingredient fix), without the
  // loading overlay.
  const refreshRecipe = useCallback(async (id) => {
    try {
      const { data } = await axios.get(`${API_BACKEND}/recipe/${id}`);
      if (data.success) setRecipeDetail(data.recipe);
    } catch {
      addToast('Could not refresh the recipe', 'error');
    }
  }, [addToast]);

  const closeDetail = useCallback(() => {
    setSelectedRecipeId(null);
    setRecipeDetail(null);
  }, []);

  // ── Shopping list ─────────────────────────────────────────────────
  const handleAddToShoppingList = useCallback((hasOpened) => {
    setShoppingDialog({ hasOpened });
  }, []);

  const handleShoppingSelect = useCallback(
    async (mode) => {
      setShoppingDialog(null);
      if (!selectedRecipeId) return;
      try {
        const { data } = await axios.post(
          `${API_BACKEND}/recipe/${selectedRecipeId}/shopping-list`,
          { mode },
        );
        if (data.success) {
          addToast(`${data.added} ingredient${data.added !== 1 ? 's' : ''} added to the shopping list`, 'success');
        } else {
          addToast(data.error || 'Could not add to the shopping list', 'error');
        }
      } catch {
        addToast('Could not add to the shopping list', 'error');
      }
    },
    [selectedRecipeId, addToast],
  );

  // ── Cook recipe (deduct from stock) ───────────────────────────────
  const handleCookClick = useCallback(() => {
    if (!recipeDetail) return;
    setCookDialog(recipeDetail);
  }, [recipeDetail]);

  const handleCookConfirm = useCallback(
    async (servings) => {
      if (!cookDialog) return;
      setCookBusy(true);
      try {
        const { data } = await axios.post(
          `${API_STORAGE}/recipes/${cookDialog.id}/cook`,
          { servings },
        );
        const deducted = data?.deducted?.length || 0;
        const shortfall = data?.shortfall_added?.length || 0;
        const unmatched = data?.unmatched?.length || 0;
        const parts = [];
        if (deducted) parts.push(`${deducted} deducted`);
        if (shortfall) parts.push(`${shortfall} to the shopping list`);
        if (unmatched) parts.push(`${unmatched} missing a unit conversion`);
        addToast(
          parts.length ? `🍳 ${parts.join(', ')}` : 'Cooked',
          unmatched ? 'error' : 'success',
        );
        setCookDialog(null);
        // Stock changed: the open recipe's colours and the cards' "missing"
        // counts are out of date.
        refreshRecipe(cookDialog.id);
        loadRecipes();
      } catch (err) {
        const detail = err?.response?.data?.detail ?? err.message;
        addToast(`Cooking failed: ${detail}`, 'error');
      } finally {
        setCookBusy(false);
      }
    },
    [cookDialog, addToast, refreshRecipe, loadRecipes],
  );

  // ── Delete recipe ─────────────────────────────────────────────────
  const handleDeleteClick = useCallback(() => {
    if (!recipeDetail) return;
    setDeleteDialog({ id: recipeDetail.id, name: recipeDetail.name });
  }, [recipeDetail]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteDialog) return;
    const { id, name } = deleteDialog;
    setDeleteDialog(null);
    try {
      await axios.delete(`${API_BACKEND}/recipe/${id}`);
      addToast(`Recipe "${name}" removed`, 'success');
      closeDetail();
      loadRecipes();
    } catch {
      addToast('Could not remove the recipe', 'error');
    }
  }, [deleteDialog, addToast, closeDetail, loadRecipes]);

  // ── Re-import ingredients from the source page ───────────────────
  const handleReimport = useCallback(async () => {
    if (!recipeDetail || reimporting) return;
    setReimporting(true);
    try {
      const { data } = await axios.post(`${API_BACKEND}/recipe/${recipeDetail.id}/reimport`);
      if (data.success) {
        const review = data.needs_review ? `, ${data.needs_review} to check` : '';
        addToast(`Ingredients updated from source (${data.ingredients} ingredient${data.ingredients !== 1 ? 's' : ''}${review})`, 'success');
        if (data.skipped?.length) addToast(skippedText(data.skipped), 'info');
        openRecipe(recipeDetail.id);
        loadRecipes();
      } else {
        addToast(data.error || 'Could not update from source', 'error');
      }
    } catch (err) {
      addToast(err?.response?.data?.error || 'Could not update from source', 'error');
    } finally {
      setReimporting(false);
    }
  }, [recipeDetail, reimporting, addToast, openRecipe, loadRecipes]);

  // ── Bundles (Sets) ────────────────────────────────────────────────
  const loadBundles = useCallback(async () => {
    setBundlesLoading(true);
    try {
      const { data } = await axios.get(`${API_STORAGE}/bundles`);
      setBundles(Array.isArray(data) ? data : []);
    } catch {
      addToast('Could not load sets', 'error');
    } finally {
      setBundlesLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (storageReady && tab === 'bundles') {
      loadBundles();
      // Product list for the editor's search picker
      axios.get(`${API_STORAGE}/products`)
        .then(({ data }) => setAllProducts(Array.isArray(data) ? data : []))
        .catch(() => {});
    }
  }, [storageReady, tab, loadBundles]);

  const openBundlePicker = useCallback(async (id) => {
    try {
      const { data } = await axios.get(`${API_STORAGE}/bundles/${id}`);
      setPickerBundle(data);
    } catch {
      addToast('Could not load the set', 'error');
    }
  }, [addToast]);

  const handleBundlePush = useCallback(async (productIds) => {
    if (!pickerBundle) return;
    setPickerBusy(true);
    try {
      const { data } = await axios.post(
        `${API_STORAGE}/bundles/${pickerBundle.id}/to-shopping`,
        { product_ids: productIds },
      );
      const parts = [`${data.added} product${data.added !== 1 ? 's' : ''} added to the shopping list`];
      if (data.skipped > 0) parts.push(`${data.skipped} already on the list`);
      addToast(parts.join(', '), 'success');
      setPickerBundle(null);
    } catch {
      addToast('Could not add to the shopping list', 'error');
    } finally {
      setPickerBusy(false);
    }
  }, [pickerBundle, addToast]);

  const openBundleEditor = useCallback(async (bundle) => {
    if (!bundle?.id) {
      setEditorBundle({});
      return;
    }
    try {
      const { data } = await axios.get(`${API_STORAGE}/bundles/${bundle.id}`);
      setEditorBundle(data);
    } catch {
      addToast('Could not load the set', 'error');
    }
  }, [addToast]);

  const handleBundleSave = useCallback(async (payload) => {
    setEditorBusy(true);
    try {
      if (editorBundle?.id) {
        await axios.put(`${API_STORAGE}/bundles/${editorBundle.id}`, payload);
      } else {
        await axios.post(`${API_STORAGE}/bundles`, payload);
      }
      addToast(`Set "${payload.name}" saved`, 'success');
      setEditorBundle(null);
      loadBundles();
    } catch {
      addToast('Could not save the set', 'error');
    } finally {
      setEditorBusy(false);
    }
  }, [editorBundle, addToast, loadBundles]);

  const handleBundleDeleteConfirm = useCallback(async () => {
    if (!bundleDeleteDialog) return;
    const { id, name } = bundleDeleteDialog;
    setBundleDeleteDialog(null);
    try {
      await axios.delete(`${API_STORAGE}/bundles/${id}`);
      addToast(`Set "${name}" removed`, 'success');
      setEditorBundle(null);
      loadBundles();
    } catch {
      addToast('Could not remove the set', 'error');
    }
  }, [bundleDeleteDialog, addToast, loadBundles]);

  // ── Render ────────────────────────────────────────────────────────
  if (!storageReady && storageChecking) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col items-center justify-center gap-4">
        <svg className="animate-spin h-8 w-8 text-emerald-400" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <p className="text-gray-400 text-sm">Waiting for the Storage add-on…</p>
        {healthRetries > 3 && (
          <p className="text-gray-500 text-xs">Attempt {healthRetries}…</p>
        )}
      </div>
    );
  }

  if (!storageReady && !storageChecking) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col items-center justify-center gap-4">
        <p className="text-red-400 text-lg">⚠️ Storage unreachable</p>
        <p className="text-gray-400 text-sm">
          Could not connect after {healthRetries} attempts.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <WhatsNewModal />
      {/* Header */}
      <header className="sticky top-0 z-30 bg-gray-900/90 backdrop-blur-md border-b border-gray-800 px-4 py-3">
        <h1 className="text-lg font-bold text-center mb-3">🍽️ Recipe</h1>

        {/* Tabs */}
        <div className="flex gap-2 mb-3">
          <button
            onClick={() => setTab('recipes')}
            className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${
              tab === 'recipes' ? 'bg-brand-cobalt text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            🍽️ Recipes
          </button>
          <button
            onClick={() => setTab('bundles')}
            className={`flex-1 py-2 rounded-xl text-sm font-semibold transition-colors ${
              tab === 'bundles' ? 'bg-brand-cobalt text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'
            }`}
          >
            🧺 Sets
          </button>
        </div>

        {/* URL input */}
        {tab === 'recipes' && (
          <div className="flex gap-2">
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleScrape()}
              placeholder="Paste a recipe URL..."
              className="flex-1 min-w-0 px-4 py-2.5 rounded-xl bg-gray-800 border border-gray-700 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              disabled={scraping}
            />
            <button
              onClick={handleScrape}
              disabled={scraping || !url.trim()}
              className="flex-shrink-0 whitespace-nowrap px-5 py-2.5 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors disabled:opacity-40"
            >
              {scraping ? (
                <span className="inline-flex items-center gap-2">
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                      fill="none"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  Importing...
                </span>
              ) : (
                'Import'
              )}
            </button>
          </div>
        )}
      </header>

      {/* Connection lost banner */}
      {disconnected && (
        <div className="mx-4 mb-2 px-4 py-3 rounded-xl bg-amber-600/90 text-white text-sm font-medium flex items-center justify-between">
          <span>⚠️ Connection lost — reload the page</span>
          <button
            onClick={() => window.location.reload()}
            className="ml-3 px-3 py-1 rounded-lg bg-white/20 hover:bg-white/30 text-xs font-semibold transition-colors"
          >
            Reload
          </button>
        </div>
      )}

      {/* Main content */}
      <main className="px-4 py-4">
        {tab === 'recipes' && (
          <>
            {loading ? (
              <div className="flex justify-center py-20">
                <div className="text-gray-500 text-sm">Loading recipes...</div>
              </div>
            ) : recipes.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <div className="text-6xl mb-4">📖</div>
                <p className="text-gray-400 text-sm">
                  No recipes yet. Paste a recipe URL above to start.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {sortRecipes(recipes).map((r) => (
                  <RecipeCard
                    key={r.id}
                    recipe={r}
                    onClick={() => openRecipe(r.id)}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'bundles' && (
          bundlesLoading ? (
            <div className="flex justify-center py-20">
              <div className="text-gray-500 text-sm">Loading sets...</div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {bundles.map((b) => (
                <BundleCard
                  key={b.id}
                  bundle={b}
                  onClick={() => openBundlePicker(b.id)}
                  onEdit={() => openBundleEditor(b)}
                />
              ))}
              {/* New bundle card */}
              <button
                onClick={() => openBundleEditor(null)}
                className="bg-gray-800/50 border-2 border-dashed border-gray-700 rounded-2xl p-4 flex flex-col items-center justify-center gap-2 text-gray-500 hover:text-gray-300 hover:border-gray-500 transition-colors min-h-[120px]"
              >
                <span className="text-3xl">＋</span>
                <span className="text-sm font-semibold">New set</span>
              </button>
            </div>
          )
        )}
      </main>

      {/* Recipe detail overlay */}
      {selectedRecipeId && !detailLoading && recipeDetail && (
        <RecipeDetail
          recipe={recipeDetail}
          onClose={closeDetail}
          onAddToShoppingList={handleAddToShoppingList}
          onCook={handleCookClick}
          onDelete={handleDeleteClick}
          onReimport={handleReimport}
          reimporting={reimporting}
          onToast={addToast}
          onFixed={() => { refreshRecipe(recipeDetail.id); loadRecipes(); }}
        />
      )}

      {/* Detail loading overlay */}
      {detailLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="text-gray-300 text-sm">Loading recipe...</div>
        </div>
      )}

      {/* Shopping list dialog */}
      {shoppingDialog && (
        <ShoppingListDialog
          hasOpened={shoppingDialog.hasOpened}
          onSelect={handleShoppingSelect}
          onClose={() => setShoppingDialog(null)}
        />
      )}

      {/* Cook dialog */}
      {cookDialog && (
        <CookDialog
          recipe={cookDialog}
          busy={cookBusy}
          onConfirm={handleCookConfirm}
          onClose={() => !cookBusy && setCookDialog(null)}
        />
      )}

      {/* Delete confirm dialog */}
      {deleteDialog && (
        <DeleteConfirmDialog
          recipeName={deleteDialog.name}
          onConfirm={handleDeleteConfirm}
          onClose={() => setDeleteDialog(null)}
        />
      )}

      {/* Bundle picker */}
      {pickerBundle && (
        <BundlePickerOverlay
          bundle={pickerBundle}
          busy={pickerBusy}
          onConfirm={handleBundlePush}
          onClose={() => !pickerBusy && setPickerBundle(null)}
        />
      )}

      {/* Bundle editor */}
      {editorBundle && (
        <BundleEditorOverlay
          bundle={editorBundle.id ? editorBundle : null}
          products={allProducts}
          busy={editorBusy}
          onSave={handleBundleSave}
          onDelete={() => setBundleDeleteDialog({ id: editorBundle.id, name: editorBundle.name })}
          onClose={() => !editorBusy && setEditorBundle(null)}
        />
      )}

      {/* Bundle delete confirm — reuse DeleteConfirmDialog copy pattern */}
      {bundleDeleteDialog && (
        <DeleteConfirmDialog
          recipeName={bundleDeleteDialog.name}
          onConfirm={handleBundleDeleteConfirm}
          onClose={() => setBundleDeleteDialog(null)}
          title="Remove set?"
          question="Remove the set"
        />
      )}

      {/* Toasts */}
      <Toasts toasts={toasts} />
    </div>
  );
}
