<template>
  <div style="padding: 24px">
    <IngestControlPanel :server-url="serverUrl" />
  </div>
</template>

<script setup lang="ts">
import IngestControlPanel from "../IngestControlPanel.vue";

const serverUrl = import.meta.env.VITE_AETUS_SERVER_URL ?? "http://127.0.0.1:18000";
</script>
