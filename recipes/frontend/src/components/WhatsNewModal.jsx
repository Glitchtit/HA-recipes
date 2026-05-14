import { useEffect, useMemo, useState } from 'react';

/* CHANGELOG entries are injected at build time by vite.config.js. Each entry
 * is { version: "X.Y.Z", body: "..." }. The build also injects __APP_ID__,
 * which scopes the localStorage key so multiple HA-apps don't trample each
 * other's last-seen markers. */
// eslint-disable-next-line no-undef
const CHANGELOG = (typeof __APP_CHANGELOG__ !== 'undefined') ? __APP_CHANGELOG__ : [];
// eslint-disable-next-line no-undef
const APP_ID = (typeof __APP_ID__ !== 'undefined') ? __APP_ID__ : 'app';
const STORAGE_KEY = `${APP_ID}_whatsnew_lastSeen`;

function cmpVersion(a, b) {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    const da = pa[i] || 0;
    const db = pb[i] || 0;
    if (da !== db) return da - db;
  }
  return 0;
}

/** Tiny markdown-bullet renderer: keeps `- item` and plain paragraphs.
 * Anything fancier (bold, links) renders as plain text — the changelog
 * format in this repo is plain bullet lists so that's all we need. */
function renderBody(body) {
  const blocks = [];
  let listItems = null;
  const flushList = () => {
    if (listItems) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="list-disc list-outside ml-5 space-y-1 text-sm text-gray-300">
          {listItems.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      );
      listItems = null;
    }
  };
  for (const line of body.split('\n')) {
    const trimmed = line.trimEnd();
    if (trimmed.startsWith('- ')) {
      if (!listItems) listItems = [];
      listItems.push(trimmed.slice(2));
    } else if (trimmed.length === 0) {
      flushList();
    } else {
      flushList();
      blocks.push(
        <p key={`p-${blocks.length}`} className="text-sm text-gray-300">{trimmed}</p>
      );
    }
  }
  flushList();
  return blocks;
}

export default function WhatsNewModal() {
  const entries = CHANGELOG;
  const currentVersion = entries[0]?.version || null;
  const [show, setShow] = useState(false);
  const [newEntries, setNewEntries] = useState([]);

  useEffect(() => {
    if (!currentVersion) return;
    const lastSeen = localStorage.getItem(STORAGE_KEY);
    if (lastSeen === currentVersion) return;
    if (!lastSeen) {
      // First visit ever — silently mark current as seen, don't show all history
      localStorage.setItem(STORAGE_KEY, currentVersion);
      return;
    }
    const toShow = entries.filter((e) => cmpVersion(e.version, lastSeen) > 0);
    if (toShow.length === 0) {
      localStorage.setItem(STORAGE_KEY, currentVersion);
      return;
    }
    setNewEntries(toShow);
    setShow(true);
  }, [currentVersion]);

  const dismiss = () => {
    if (currentVersion) localStorage.setItem(STORAGE_KEY, currentVersion);
    setShow(false);
  };

  if (!show || newEntries.length === 0) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={dismiss}
    >
      <div
        className="bg-gray-900 border border-orange-500/40 rounded-2xl max-w-xl w-full max-h-[80vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-gray-900/95 backdrop-blur-md border-b border-gray-800 px-6 pt-5 pb-3 flex items-center justify-between">
          <div>
            <div className="text-2xl">🎉</div>
            <h2 className="text-xl font-display text-orange-400 mt-1">What's new</h2>
            <p className="text-xs text-gray-400">
              {newEntries.length === 1
                ? `Version ${newEntries[0].version}`
                : `${newEntries.length} updates since you last visited`}
            </p>
          </div>
          <button
            onClick={dismiss}
            className="text-gray-500 hover:text-gray-200 text-xl px-2"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="px-6 py-4 space-y-5">
          {newEntries.map((e) => (
            <section key={e.version} className="space-y-2">
              <h3 className="text-sm font-semibold text-orange-300">v{e.version}</h3>
              <div className="space-y-2">{renderBody(e.body)}</div>
            </section>
          ))}
        </div>

        <div className="sticky bottom-0 bg-gray-900/95 backdrop-blur-md border-t border-gray-800 px-6 py-3 flex justify-end">
          <button
            onClick={dismiss}
            className="bg-orange-500 hover:bg-orange-400 text-white text-sm rounded-xl px-4 py-2"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
