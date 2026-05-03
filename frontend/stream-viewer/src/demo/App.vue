<template>
  <AetusStreamViewer
    query-server-url="/"
    device-id="dense-device-1"
    initial-stream-key="dense.vibration"
    initial-range-preset="10m"
    :max-points-per-request="10000"
  />
</template>

<script setup lang="ts">
import AetusStreamViewer from "../AetusStreamViewer.vue";
</script>
