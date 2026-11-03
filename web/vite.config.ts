import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The server serves `dist/`. In dev, API calls go to a running server (`EVALUTION_API`).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/v1": process.env.EVALUTION_API ?? "http://127.0.0.1:8000" },
  },
});
