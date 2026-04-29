# AETUS ESP32-C5 HIL Upload Firmware

This ESP-IDF 6.0 firmware is for local HIL/lab validation. It consumes the portable stack in `../esp32-aetus` and keeps business logic separate from upload transport:

- `main` creates sample telemetry messages and enqueues them.
- `aetus` owns the FreeRTOS queue, upload timer, Wi-Fi connection, RTC sync, nanopb encoding, and HTTP POST to `/v1/ingest`.
- The sample payload covers status, double, int64, bool, and string metric values.
- The app calls authenticated `GET /v1/time` first, then includes RTC-derived `timestamp_ns` in status and telemetry events.

Configuration is read from environment variables at CMake configure time. Keep secrets in the untracked repository-level `.env.hil` file.

```bash
set -a
source ../../.env.hil
set +a
source "$IDF_PATH/export.sh"
idf.py set-target esp32c5
idf.py -p "$AETUS_SERIAL_PORT" flash monitor
```
