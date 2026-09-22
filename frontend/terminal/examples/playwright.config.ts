import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
const cwd = fileURLToPath(new URL("..", import.meta.url));
const python = process.platform === "win32" ? `"${resolve(cwd, "../../.venv/Scripts/python.exe")}"` : "python";
export default defineConfig({
  testDir: ".", testMatch: "live.spec.ts", workers: 1, timeout: 120000,
  use: { baseURL: "http://localhost:5176", viewport: { width: 1440, height: 1100 } },
  webServer: [
    { cwd, command: `node node_modules/vite/bin/vite.js --config examples/vite.config.ts`,
      url: "http://localhost:5176", reuseExistingServer: true },
    { cwd, command: `${python} ../../tests/trading/us_live_browser.py --root ../../tmp/task5-e2e-${randomUUID()}`,
      url: "http://127.0.0.1:8775/health", reuseExistingServer: true },
  ],
});
