<template>
  <AetusAnomalyPanel
    anomaly-server-url="http://127.0.0.1:18002"
    auth-token="e2e-anomaly-admin-token"
    :auto-refresh-ms="15000"
  />
</template>

<script setup lang="ts">
import AetusAnomalyPanel from "../AetusAnomalyPanel.vue";
</script>
