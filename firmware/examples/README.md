# AETUS Firmware Examples

These examples are real ESP-IDF apps that consume the portable stack in `firmware/esp32-aetus`.

They are intended to validate the embedded developer experience:

- user code includes only `aetus.h`
- user tasks call thread-safe enqueue APIs
- the shared `aetus` component owns protobuf encoding, queueing, upload timer, Wi-Fi, and HTTP transport

## Examples

- `basic-telemetry`: minimal boot status plus one telemetry event.
- `multitask-producers`: two producer tasks enqueue concurrently to validate the thread-safe API shape.
- `metric-types`: telemetry using all supported protobuf `oneof` metric types.

## Flash And Partition Assumption

The examples assume a minimum 4MB external SPI flash device.

Each example uses a custom partition table with a 3MB factory app partition. This is intentionally larger than ESP-IDF's default single-app layout because the AETUS upload stack pulls in Wi-Fi, HTTP client, mbedTLS, protobuf encoding, and diagnostics-adjacent dependencies.

The examples do not enable OTA slots yet. Product firmware can split the same 4MB flash into OTA partitions later, but these examples optimize for the simplest possible first compile and flash path.

## Build

```bash
source /Users/donghoonpark/.espressif/v6.0/esp-idf/export.sh
idf.py -C firmware/examples/basic-telemetry set-target esp32c5 build
idf.py -C firmware/examples/multitask-producers set-target esp32c5 build
idf.py -C firmware/examples/metric-types set-target esp32c5 build
```

The examples use placeholder credentials. Replace the constants in `main/main.c` before flashing to real hardware.
