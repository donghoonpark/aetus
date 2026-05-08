<template>
  <AetusStreamViewer
    query-server-url="/"
    auth-token="viewer-token"
    :initial-device-ids="['dense-device-1', 'dense-device-2']"
    initial-stream-key="dense.vibration"
    initial-range-preset="10m"
    :max-points-per-request="10000"
  />
</template>

<script setup lang="ts">
import AetusStreamViewer from "../AetusStreamViewer.vue";
</script>
