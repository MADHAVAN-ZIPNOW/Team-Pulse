import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies API + auth calls to the backend so the browser only talks to :5173.
// Tokens never reach the browser: the session lives in an HTTP-only cookie set by the backend.

export default defineConfig(({ mode }) => {
  // Optional overrides for machines where 8000 / 5173 are taken: BACKEND_URL, FRONTEND_PORT.
  const env = loadEnv(mode, ".", "");
  const backend = env.BACKEND_URL || "http://localhost:8000";
  return {
  plugins: [react(), tailwindcss()],
  server: {
    port: Number(env.FRONTEND_PORT) || 5173,
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
};
});
