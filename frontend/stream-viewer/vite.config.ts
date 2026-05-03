import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:18001",
    },
  },
  build: {
    sourcemap: true,
    lib: {
      entry: resolve(__dirname, "src/index.ts"),
      name: "AetusStreamViewer",
      fileName: "aetus-stream-viewer",
    },
    rollupOptions: {
      external: (id) => id === "vue" || id === "naive-ui" || id === "echarts" || id.startsWith("echarts/"),
      output: {
        exports: "named",
        globals: {
          vue: "Vue",
          "naive-ui": "naive",
          echarts: "echarts",
          "echarts/core": "echarts",
          "echarts/components": "echarts",
          "echarts/charts": "echarts",
          "echarts/renderers": "echarts",
        },
      },
    },
  },
});
