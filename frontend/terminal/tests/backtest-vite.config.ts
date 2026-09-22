import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
export default defineConfig({
  root: fileURLToPath(new URL("..", import.meta.url)),
  server: { host: "127.0.0.1", port: 5178, strictPort: true,
    proxy: { "/api": "http://127.0.0.1:18764" } },
  build: { outDir: "../../tmp/backtest-preview", emptyOutDir: false,
    rollupOptions: { input: fileURLToPath(new URL("../backtest-preview.html", import.meta.url)) } },
});
