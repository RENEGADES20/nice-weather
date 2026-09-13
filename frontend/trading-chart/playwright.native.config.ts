import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "terminal-flow.spec.ts",
  timeout: 90_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8513" },
  globalSetup: "./e2e/serve_native.mjs",
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } } },
  ],
});
