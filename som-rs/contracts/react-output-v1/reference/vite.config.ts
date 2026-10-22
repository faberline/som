import { defineConfig } from "vitest/config";

const target = process.env.SOM_API_URL ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": {
    target,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  server: { proxy },
  preview: { proxy },
  test: { environment: "jsdom" },
});
