import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dashboard talks to the API through same-origin relative URLs, so the dev
// server proxies both the REST plane and the telemetry WebSocket to FastAPI.
const target = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
