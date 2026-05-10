import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  build: {
    lib: {
      entry: "src/index.ts",
      name: "AetusAnomalyPanel",
      fileName: "aetus-anomaly-panel",
    },
    rollupOptions: {
      external: ["vue", "naive-ui", "lucide-vue-next"],
      output: {
        exports: "named",
        globals: {
          vue: "Vue",
          "naive-ui": "naive",
          "lucide-vue-next": "LucideVueNext",
        },
      },
    },
  },
});
