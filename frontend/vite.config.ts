import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the page and the API share an origin through this proxy, so
// the API needs no CORS settings. API_URL points it somewhere other than the
// local stack.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.API_URL ?? "http://localhost:8000",
    },
  },
});
