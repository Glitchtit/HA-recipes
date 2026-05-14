import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import WhatsNewModal from './components/WhatsNewModal';

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
      </div>
      <div className="p-3">
        <h3 className="font-semibold text-gray-100 text-sm line-clamp-2">
          {recipe.name}
        </h3>
        <p className="text-gray-400 text-xs mt-1">
          {recipe.servings} annos{recipe.servings !== 1 ? 'ta' : ''}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// RecipeDetail — full-screen overlay
// ---------------------------------------------------------------------------
function RecipeDetail({ recipe, onClose, onAddToShoppingList, onCook, onDelete, onToast }) {
  if (!recipe) return null;

  const imgUrl = recipeImageUrl(recipe.picture_filename);
  const hasOpened = recipe.ingredients?.some(
    (i) => i.status === 'yellow',
  );

  const [printing, setPrinting] = useState(false);
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
      onToast?.('Tulostettu', 'success');
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'tuntematon virhe';
      onToast?.(`Tulostus epäonnistui: ${msg}`, 'error');
    } finally {
      setPrinting(false);
    }
  }, [recipe, imgUrl, printing, onToast]);

  return (
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
            {recipe.servings} annos{recipe.servings !== 1 ? 'ta' : ''}
            {recipe.source_url && (
              <>
                {' · '}
                <a
                  href={recipe.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-emerald-400 hover:underline"
                >
                  Lähde
                </a>
              </>
            )}
          </p>
        </div>

        {/* Ingredients */}
        <div className="px-6 pb-4">
          <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wide mb-2">
            Ainekset
          </h3>
          <ul className="space-y-1">
            {recipe.ingredients?.map((ing) => (
              <li
                key={ing.id}
                className={`text-sm px-3 py-2 rounded-lg ${
                  ing.status === 'green'
                    ? 'bg-brand-orange text-white'
                    : ing.status === 'yellow'
                      ? 'bg-amber-900/40 text-amber-300'
                      : 'bg-red-900/40 text-red-300'
                }`}
              >
                <span className="font-medium">{ing.product_name}</span>
                {ing.amount_needed > 0 && (
                  <span className="text-xs opacity-70 ml-2">
                    — {ing.amount_needed}{ing.unit_abbrev ? ` ${ing.unit_abbrev}` : ' kpl'}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>

        {/* Instructions */}
        {recipe.instructions?.length > 0 && (
          <div className="px-6 pb-4">
            <h3 className="text-sm font-bold text-gray-300 uppercase tracking-wide mb-2">
              Ohjeet
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
            🍳 Tee resepti (vähennä varastosta)
          </button>
          <button
            onClick={() => onAddToShoppingList(hasOpened)}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors"
          >
            Lisää ostoslistalle
          </button>
          <button
            onClick={handlePrint}
            disabled={printing}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 disabled:hover:bg-gray-700 transition-colors"
          >
            {printing ? '🖨 Tulostetaan…' : '🖨 Tulosta kuittipaperille'}
          </button>
          <button
            onClick={onDelete}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Poista resepti
          </button>
        </div>
      </div>
    </div>
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
          Lisää ostoslistalle
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          Mitkä ainekset lisätään?
        </p>
        <div className="space-y-2">
          <button
            onClick={() => onSelect('missing')}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Vain puuttuvat
          </button>
          {hasOpened && (
            <button
              onClick={() => onSelect('missing_and_opened')}
              className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-amber-500 hover:bg-amber-600 active:bg-amber-700 transition-colors"
            >
              Puuttuvat + avatut
            </button>
          )}
          <button
            onClick={() => onSelect('all')}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors"
          >
            Kaikki ainekset
          </button>
          <button
            onClick={onClose}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Peruuta
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
          Tee resepti
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          Vähennä aineet varastosta — puuttuvat lisätään ostoslistalle.
        </p>
        <label className="block text-sm text-gray-300 mb-1">Annoksia</label>
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
            {busy ? 'Vähennetään…' : 'Vahvista'}
          </button>
          <button
            onClick={onClose}
            disabled={busy}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 disabled:opacity-50 transition-colors"
          >
            Peruuta
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DeleteConfirmDialog
// ---------------------------------------------------------------------------
function DeleteConfirmDialog({ recipeName, onConfirm, onClose }) {
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
          Poista resepti?
        </h3>
        <p className="text-gray-400 text-sm text-center mb-5">
          Haluatko varmasti poistaa reseptin{' '}
          <span className="font-semibold text-gray-200">"{recipeName}"</span>?
        </p>
        <div className="space-y-2">
          <button
            onClick={onConfirm}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-red-500 hover:bg-red-600 active:bg-red-700 transition-colors"
          >
            Poista
          </button>
          <button
            onClick={onClose}
            className="w-full py-2 rounded-xl font-semibold text-gray-400 text-sm hover:text-gray-200 transition-colors"
          >
            Peruuta
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
  const [url, setUrl] = useState('');
  const [toasts, setToasts] = useState([]);

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
      addToast('Reseptien lataus epäonnistui', 'error');
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
        addToast(`Resepti "${data.name}" tallennettu!`, 'success');
        setUrl('');
        loadRecipes();
      } else {
        addToast(data.error || 'Reseptin haku epäonnistui', 'error');
      }
    } catch (err) {
      const msg =
        err?.response?.data?.error || 'Reseptin haku epäonnistui';
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
          addToast('Reseptin lataus epäonnistui', 'error');
          setSelectedRecipeId(null);
        }
      } catch {
        addToast('Reseptin lataus epäonnistui', 'error');
        setSelectedRecipeId(null);
      } finally {
        setDetailLoading(false);
      }
    },
    [addToast],
  );

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
          addToast(`${data.added} ainesta lisätty ostoslistalle`, 'success');
        } else {
          addToast(data.error || 'Ostoslistalle lisäys epäonnistui', 'error');
        }
      } catch {
        addToast('Ostoslistalle lisäys epäonnistui', 'error');
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
        if (deducted) parts.push(`${deducted} vähennetty`);
        if (shortfall) parts.push(`${shortfall} ostoslistalle`);
        if (unmatched) parts.push(`${unmatched} muunnos puuttuu`);
        addToast(
          parts.length ? `🍳 ${parts.join(', ')}` : 'Resepti tehty',
          unmatched ? 'error' : 'success',
        );
        setCookDialog(null);
      } catch (err) {
        const detail = err?.response?.data?.detail ?? err.message;
        addToast(`Reseptin teko epäonnistui: ${detail}`, 'error');
      } finally {
        setCookBusy(false);
      }
    },
    [cookDialog, addToast],
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
      addToast(`Resepti "${name}" poistettu`, 'success');
      closeDetail();
      loadRecipes();
    } catch {
      addToast('Reseptin poisto epäonnistui', 'error');
    }
  }, [deleteDialog, addToast, closeDetail, loadRecipes]);

  // ── Render ────────────────────────────────────────────────────────
  if (!storageReady && storageChecking) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col items-center justify-center gap-4">
        <svg className="animate-spin h-8 w-8 text-emerald-400" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <p className="text-gray-400 text-sm">Odotetaan Storage-lisäosaa…</p>
        {healthRetries > 3 && (
          <p className="text-gray-500 text-xs">Yritys {healthRetries}…</p>
        )}
      </div>
    );
  }

  if (!storageReady && !storageChecking) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col items-center justify-center gap-4">
        <p className="text-red-400 text-lg">⚠️ Storage ei tavoitettavissa</p>
        <p className="text-gray-400 text-sm">
          Yhteyden muodostaminen epäonnistui {healthRetries} yrityksen jälkeen.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm"
        >
          Yritä uudelleen
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

        {/* URL input */}
        <div className="flex gap-2">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleScrape()}
            placeholder="Liitä reseptin URL..."
            className="flex-1 px-4 py-2.5 rounded-xl bg-gray-800 border border-gray-700 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            disabled={scraping}
          />
          <button
            onClick={handleScrape}
            disabled={scraping || !url.trim()}
            className="px-5 py-2.5 rounded-xl font-semibold text-white text-sm bg-brand-cobalt hover:bg-brand-cobalt-400 active:bg-brand-cobalt-600 transition-colors disabled:opacity-40"
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
                Haetaan...
              </span>
            ) : (
              'Hae'
            )}
          </button>
        </div>
      </header>

      {/* Connection lost banner */}
      {disconnected && (
        <div className="mx-4 mb-2 px-4 py-3 rounded-xl bg-amber-600/90 text-white text-sm font-medium flex items-center justify-between">
          <span>⚠️ Yhteys katkesi — lataa sivu uudelleen</span>
          <button
            onClick={() => window.location.reload()}
            className="ml-3 px-3 py-1 rounded-lg bg-white/20 hover:bg-white/30 text-xs font-semibold transition-colors"
          >
            Lataa uudelleen
          </button>
        </div>
      )}

      {/* Main content */}
      <main className="px-4 py-4">
        {loading ? (
          <div className="flex justify-center py-20">
            <div className="text-gray-500 text-sm">Ladataan reseptejä...</div>
          </div>
        ) : recipes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="text-6xl mb-4">📖</div>
            <p className="text-gray-400 text-sm">
              Ei reseptejä vielä. Liitä reseptin URL ylhäällä aloittaaksesi!
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {recipes.map((r) => (
              <RecipeCard
                key={r.id}
                recipe={r}
                onClick={() => openRecipe(r.id)}
              />
            ))}
          </div>
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
          onToast={addToast}
        />
      )}

      {/* Detail loading overlay */}
      {detailLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="text-gray-300 text-sm">Ladataan reseptiä...</div>
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

      {/* Toasts */}
      <Toasts toasts={toasts} />
    </div>
  );
}
