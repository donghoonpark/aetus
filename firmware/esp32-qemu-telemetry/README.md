# AETUS ESP32 QEMU Telemetry Firmware

This ESP-IDF app is intentionally small and test-oriented. It compiles a nanopb encoder for the current AETUS ingest protobuf schema, emits one telemetry payload as a hex-framed UART stream, and lets the Python `qemu_e2e` test POST those exact bytes to the ingest API.

The production embedded standard remains ESP32-C5, but ESP-IDF 6.0 QEMU currently does not support the `esp32c5` target. The default QEMU target is therefore `esp32c3`, which still exercises a RISC-V ESP32 firmware binary running the same nanopb encode path. The test runner can override the target with `AETUS_QEMU_TARGET` once C5 QEMU support becomes available.

```bash
source "$IDF_PATH/export.sh"
idf.py set-target esp32c3
idf.py qemu monitor
```
