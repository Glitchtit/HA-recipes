import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

// ---------------------------------------------------------------------------
// Ingress-path awareness
// ---------------------------------------------------------------------------
const INGRESS_PATH =
  document.querySelector('meta[name="ingress-path"]')?.content ?? '';

const API_GROCY = `${INGRESS_PATH}/api/grocy`;
const API_BACKEND = `${INGRESS_PATH}/api/backend`;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function recipeImageUrl(filename) {
  if (!filename) return null;
  const encoded = btoa(filename);
  return `${INGRESS_PATH}/api/grocy-files/recipepictures/${encoded}?force_serve_as=picture&best_fit_width=400`;
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
  const imgUrl = recipeImageUrl(recipe.picture_file_name);
  return (
    <button
      onClick={onClick}
      className="bg-gray-800 rounded-2xl shadow-lg overflow-hidden text-left hover:ring-2 hover:ring-emerald-400 transition-all active:scale-[0.98]"
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
          {recipe.base_servings} annos{recipe.base_servings !== 1 ? 'ta' : ''}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// RecipeDetail — full-screen overlay
// ---------------------------------------------------------------------------
function RecipeDetail({ recipe, onClose, onAddToShoppingList, onDelete }) {
  if (!recipe) return null;

  const imgUrl = recipeImageUrl(recipe.picture_file_name);
  const hasOpened = recipe.ingredients?.some(
    (i) => i.status === 'yellow',
  );

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
            {recipe.base_servings} annos{recipe.base_servings !== 1 ? 'ta' : ''}
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
                    ? 'bg-emerald-900/40 text-emerald-300'
                    : ing.status === 'yellow'
                      ? 'bg-amber-900/40 text-amber-300'
                      : 'bg-red-900/40 text-red-300'
                }`}
              >
                <span className="font-medium">{ing.product_name}</span>
                {ing.amount_needed > 0 && (
                  <span className="text-xs opacity-70 ml-2">
                    ({ing.amount_in_stock}/{ing.amount_needed}
                    {ing.amount_opened > 0 && `, ${ing.amount_opened} avattu`})
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
            <ol className="space-y-2 text-sm text-gray-300 list-decimal list-inside">
              {recipe.instructions.map((step, i) => (
                <li key={i} className="leading-relaxed">
                  {step}
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Action buttons */}
        <div className="px-6 pb-6 space-y-2">
          <button
            onClick={() => onAddToShoppingList(hasOpened)}
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-emerald-500 hover:bg-emerald-600 active:bg-emerald-700 transition-colors"
          >
            Lisää ostoslistalle
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
            className="w-full py-3 rounded-xl font-semibold text-white text-sm bg-emerald-500 hover:bg-emerald-600 active:bg-emerald-700 transition-colors"
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
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scraping, setScraping] = useState(false);
  const [url, setUrl] = useState('');
  const [toasts, setToasts] = useState([]);

  // Detail view
  const [selectedRecipeId, setSelectedRecipeId] = useState(null);
  const [recipeDetail, setRecipeDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Dialogs
  const [shoppingDialog, setShoppingDialog] = useState(null); // {hasOpened: bool}
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
    loadRecipes();
  }, [loadRecipes]);

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
  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      {/* Header */}
      <header className="sticky top-0 z-30 bg-gray-900/90 backdrop-blur-md border-b border-gray-800 px-4 py-3">
        <h1 className="text-lg font-bold text-center mb-3">🍽️ Grocy Recipes</h1>

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
            className="px-5 py-2.5 rounded-xl font-semibold text-white text-sm bg-emerald-500 hover:bg-emerald-600 active:bg-emerald-700 transition-colors disabled:opacity-40"
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
          onDelete={handleDeleteClick}
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
