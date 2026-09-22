import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: ".", testMatch: "backtest.spec.ts", workers: 1,
  reporter: "list", use: { baseURL: "http://127.0.0.1:5178", viewport: { width: 1440, height: 1050 } },
  webServer: { command: "node node_modules/vite/bin/vite.js --config tests/backtest-vite.config.ts",
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    url: "http://127.0.0.1:5178", reuseExistingServer: true },
});
