import { defineConfig } from "vite";
export default defineConfig({
  build: { outDir: "../../src/nice_weather/terminal_dist", emptyOutDir: false },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8767", ws: true } } },
});
