<template>
  <AetusStreamViewer
    query-server-url="http://127.0.0.1:18001"
    device-id="query-device-1"
    initial-stream-key="imu.accel"
  />
</template>

<script setup lang="ts">
import AetusStreamViewer from "../AetusStreamViewer.vue";
</script>
