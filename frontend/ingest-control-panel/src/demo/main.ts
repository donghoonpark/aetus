import { createApp } from "vue";
import { NConfigProvider } from "naive-ui";
import App from "./App.vue";

const app = createApp({
  components: { App, NConfigProvider },
  template: `
    <n-config-provider>
      <App />
    </n-config-provider>
  `,
});

app.mount("#app");
