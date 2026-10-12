import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Built into the Python package and served by Starlette at /app/.
// Dev: scripts/dev-rabbit.sh mac (backend on :8001 + this dev server on :5173).
const backend = 'http://localhost:8001' // scripts/dev-rabbit.sh mac
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url))

export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': here('./src') } },
  build: {
    outDir: here('../src/garmin_mcp/web_dist'),
    emptyOutDir: true,
    assetsInlineLimit: 0, // keep CSP simple: no data: fonts/scripts
  },
  server: {
    proxy: {
      '/app/api': {
        target: backend,
        // The backend only accepts mutating calls whose Origin is its own.
        configure: (proxy) => proxy.on('proxyReq', (req) => req.setHeader('origin', backend)),
      },
      '/app/auth': { target: backend },
    },
  },
})
