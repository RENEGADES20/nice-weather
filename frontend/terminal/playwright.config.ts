import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "test-results/report.json" }]],
  use: {
    baseURL: "http://127.0.0.1:5175",
    viewport: { width: 1440, height: 1100 },
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5175",
    url: "http://127.0.0.1:5175",
    reuseExistingServer: false,
  },
});
