import { defineConfig } from 'vite';

// Dev: `npm run dev` proxies API calls to a locally running FastAPI server (uvicorn api.main:app --port 8000).
export default defineConfig({
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
