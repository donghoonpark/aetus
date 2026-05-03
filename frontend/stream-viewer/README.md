# @aetus/stream-viewer

Portable Vue 3 stream viewer for the AETUS query API.

The component intentionally knows only the query-api base URL, a device ID, and an optional initial stream key. It treats scalar metrics and sampled signal frames as one logical stream model so host applications do not need to understand the PostgreSQL storage layout.

## Usage

```vue
<script setup lang="ts">
import { AetusStreamViewer } from "@aetus/stream-viewer";
import "@aetus/stream-viewer/style.css";
</script>

<template>
  <AetusStreamViewer
    query-server-url="http://127.0.0.1:18001"
    device-id="dense-device-1"
    initial-stream-key="dense.vibration"
    initial-range-preset="10m"
    :max-points-per-request="10000"
  />
</template>
```

## Props

| Prop | Required | Description |
| --- | --- | --- |
| `queryServerUrl` | yes | Base URL for `services/query-api`. |
| `deviceId` | no | Initial device ID. Operators can edit it in the component. |
| `initialStreamKey` | no | Initial stream key to select after stream metadata loads. |
| `initialRangePreset` | no | Initial range preset: `10m`, `1h`, `6h`, or `1d`; defaults to `1h`. |
| `maxPointsPerRequest` | no | Chart point budget sent as `max_points`; defaults to `1500`. |

## Development

```bash
npm install
npm run build
npm run test:e2e
```

The Playwright e2e suite mocks query-api responses and verifies that the component can render both sampled and scalar streams using only the `queryServerUrl` contract.
