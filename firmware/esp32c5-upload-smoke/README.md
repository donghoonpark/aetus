# AETUS ESP32-C5 Upload Smoke Firmware

This ESP-IDF 6.0 firmware is for local HIL/lab validation only. It keeps business logic separate from upload transport:

- `main` creates sample telemetry messages and enqueues them.
- `aetus_uploader` owns the FreeRTOS queue, upload timer, WiFi connection, nanopb encoding, and HTTP POST to `/v1/ingest`.

Configuration is read from environment variables at CMake configure time. Keep secrets in the untracked repository-level `.env.hil` file.

```bash
set -a
source ../../.env.hil
set +a
source "$IDF_PATH/export.sh"
idf.py set-target esp32c5
idf.py -p "$AETUS_SERIAL_PORT" flash monitor
```
