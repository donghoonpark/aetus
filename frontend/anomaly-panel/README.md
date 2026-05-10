# AETUS Anomaly Panel

Embeddable Vue control panel for the AETUS anomaly detection service. It is intentionally small: pass the anomaly API URL and an admin token, then mount the component inside an operator UI.

```vue
<template>
  <AetusAnomalyPanel
    anomaly-server-url="https://aetus.example.internal"
    auth-token="..."
    :auto-refresh-ms="15000"
  />
</template>

<script setup lang="ts">
import { AetusAnomalyPanel } from "@aetus/anomaly-panel";
import "@aetus/anomaly-panel/style.css";
</script>
```

## Capabilities

- Lists configured threshold detection jobs.
- Lists recent anomaly events and score/threshold pairs.
- Lists webhook endpoints used for alert fan-out.
- Creates a threshold detector job from device ID, stream key, operator, and threshold.

The panel calls the anomaly API with `x-aetus-admin-token`; production embedding code should inject that token from the host application rather than hard-coding it.
