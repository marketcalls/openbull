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
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/auth": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/web": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/upstox": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/zerodha": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
})
