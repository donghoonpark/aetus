import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [vue()],
  build: {
    sourcemap: true,
    lib: {
      entry: resolve(__dirname, "src/index.ts"),
      name: "AetusIngestControlPanel",
      fileName: "aetus-ingest-control-panel",
    },
    rollupOptions: {
      external: ["vue", "naive-ui"],
      output: {
        exports: "named",
        globals: {
          vue: "Vue",
          "naive-ui": "naive",
        },
      },
    },
  },
});
