import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Honoured by scripts/start-frontend.sh so the dev server can point at a
// backend on another host or port without editing this file.
const target = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': target,
      '/ws': { target: target.replace(/^http/, 'ws'), ws: true },
    },
  },
})
