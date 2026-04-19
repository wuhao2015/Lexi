import { defineConfig } from "vite";

// Vercel sets VERCEL=1 during build; output must stay inside the project root there.
const outDir = process.env.VERCEL ? "dist" : "../backend/static";

export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir,
    emptyOutDir: true,
  },
});
