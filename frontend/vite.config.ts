import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// API and WebSocket calls are proxied to the FastAPI backend, so the frontend
// only ever talks to its own origin (same pattern as nginx in production).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Listen on all interfaces so other devices on the LAN can reach the dev server
    host: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
      // MCP endpoint and its OAuth flow (no prefix rewrite, same as nginx)
      '/mcp': { target: 'http://127.0.0.1:8000' },
      '/oauth': { target: 'http://127.0.0.1:8000' },
      '/authorize': { target: 'http://127.0.0.1:8000' },
      '/token': { target: 'http://127.0.0.1:8000' },
      '/register': { target: 'http://127.0.0.1:8000' },
      '/revoke': { target: 'http://127.0.0.1:8000' },
      '/.well-known': { target: 'http://127.0.0.1:8000' },
    },
  },
})
