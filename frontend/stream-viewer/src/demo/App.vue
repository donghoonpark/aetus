<template>
  <AetusStreamViewer
    :query-server-url="queryServerUrl"
    :auth-token="authToken"
    :initial-device-ids="['dense-device-1', 'dense-device-2']"
    initial-stream-key="dense.vibration"
    initial-range-preset="10m"
    :max-points-per-request="10000"
  />
</template>

<script setup lang="ts">
import AetusStreamViewer from "../AetusStreamViewer.vue";

const queryServerUrl = import.meta.env.VITE_AETUS_QUERY_SERVER_URL ?? "/";
const authToken = import.meta.env.VITE_AETUS_QUERY_AUTH_TOKEN ?? "viewer-token";
</script>
