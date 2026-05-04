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
- `cpp-friendly-interface`: C++20 wrapper usage from a repository-level example app.
- `cpp-basic`: C++20 wrapper, RTC sync, status event, telemetry metrics, BLE provisioning, and immediate flush.
- `cpp-signal-frame`: C++20 dense signal frame upload example.
- `cpp-light-sleep`: C++20 power-management example with tickless idle and automatic light sleep enabled.

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
idf.py -C firmware/examples/cpp-friendly-interface set-target esp32c5 build
idf.py -C firmware/examples/cpp-basic set-target esp32c5 build
idf.py -C firmware/examples/cpp-signal-frame set-target esp32c5 build
idf.py -C firmware/examples/cpp-light-sleep set-target esp32c5 build
```

The basic C examples use placeholder credentials. Replace the constants in `main/main.c` before flashing to real hardware.

The C++ HIL-oriented examples read Wi-Fi/API credentials from environment variables at CMake configure time:

- `AETUS_WIFI_SSID`
- `AETUS_WIFI_PASSWORD`
- `AETUS_INGEST_URL`
- `AETUS_DEVICE_ID`
- `AETUS_DEVICE_TOKEN`

For compile-only CI, these are supplied with dummy values.

## Signal Frame Size

`cpp-signal-frame` uses the AETUS component's static signal frame budget. The default maximum raw sample payload is 2048 bytes. To validate larger frames, such as a 2400 byte dense sample block, add this to the example's `sdkconfig.defaults` or set it through `idf.py menuconfig`:

```ini
CONFIG_AETUS_SIGNAL_SAMPLES_MAX=2400
```

If this value becomes inconsistent with the protobuf encode buffer or queue slot memory budget, the component fails at compile time instead of producing a firmware image with surprising RAM usage.
