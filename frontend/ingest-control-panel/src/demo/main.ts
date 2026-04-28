import { createApp, h } from "vue";
import { NConfigProvider } from "naive-ui";
import App from "./App.vue";

const app = createApp({
  components: { App, NConfigProvider },
  render: () => h(NConfigProvider, null, { default: () => h(App) }),
});

app.mount("#app");
