import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadChangelogEntries() {
  try {
    const raw = readFileSync(resolve(__dirname, '../CHANGELOG.md'), 'utf-8');
    const entries = [];
    let current = null;
    for (const line of raw.split('\n')) {
      const m = line.match(/^##\s+(\d+\.\d+\.\d+)\s*$/);
      if (m) {
        if (current) entries.push(current);
        current = { version: m[1], body: [] };
      } else if (current) {
        current.body.push(line);
      }
    }
    if (current) entries.push(current);
    return entries.map((e) => ({ version: e.version, body: e.body.join('\n').trim() }));
  } catch (e) {
    return [];
  }
}

export default defineConfig({
  plugins: [react()],
  base: './',
  define: {
    __APP_CHANGELOG__: JSON.stringify(loadChangelogEntries()),
    __APP_ID__: JSON.stringify('recipes'),
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
});
