import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies API + auth calls to the backend so the browser only talks to :5173.
// Tokens never reach the browser: the session lives in an HTTP-only cookie set by the backend.
const backend = "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backend },
      "/auth/login": { target: backend },
      "/auth/me": { target: backend },
      "/auth/cli": { target: backend },
      "/auth/az": { target: backend },
      "/auth/callback": { target: backend }, // lets AZURE_REDIRECT_URI point at :5173 too
      "/auth/logout": { target: backend },
    },
  },
});
