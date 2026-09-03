import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  base: "./",
  build: {
    outDir: resolve(__dirname, "../../src/nice_weather/trading_chart_dist"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
