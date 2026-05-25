import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Pre-bundle the Plotly CJS bundles so dev and prod see identical default-
  // export shapes — without this Vite occasionally returns the namespace
  // object instead of the default function and the chart fails to mount.
  optimizeDeps: {
    include: ["react-plotly.js/factory", "plotly.js-cartesian-dist-min"],
  },
  server: {
    host: "127.0.0.1", // force IPv4 loopback — Node on Windows binds "localhost" to ::1-only
    port: 5173,
    strictPort: true,
    proxy: {
      // Trailing slashes prevent accidental prefix matches — e.g. the bare
      // "/web" rule used to swallow "/websocket/test" because it starts with
      // "/web" — causing the browser to hit FastAPI instead of Vite's SPA.
      "/api/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/auth/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/web/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/upstox/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/zerodha/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      // Strategy module WebSocket — proxied with ws:true so the upgrade
      // handshake is forwarded to the backend. Without this Vite serves
      // the SPA's index.html for /ws/strategy/{id} and the browser sees
      // an immediate close (or just hangs at opening) — which was the
      // whole reason live PnL never streamed in dev.
      "/ws/": {
        target: "ws://127.0.0.1:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
