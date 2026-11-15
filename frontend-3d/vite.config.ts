import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: `npm run dev` on :5173, proxying /api to the FastAPI backend on :8000.
// Build: served by FastAPI at /explorer (see backend/polgraph/api.py).
export default defineConfig(({ command }) => ({
  base: command === "build" ? "/explorer/" : "/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
}));
