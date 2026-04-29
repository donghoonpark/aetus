# ESP32 AETUS Embedded Upload Stack

`firmware/esp32-aetus` is the portable ESP-IDF component set for AETUS devices.

The goal is deliberately simple for product firmware authors:

1. Initialize AETUS once during boot.
2. Build telemetry/status structs in business logic tasks.
3. Call the thread-safe enqueue API.
4. Let the dedicated uploader task handle queueing, protobuf encoding, Wi-Fi, and HTTP upload.

## Components

```text
firmware/esp32-aetus/
  components/
    aetus/      # Thread-safe upload API, uploader task, nanopb encode, HTTP client
    nanopb/     # Minimal nanopb runtime used by the aetus component
  examples/
    cpp-basic/        # Standalone ESP-IDF app showing the C++20 wrapper API
    cpp-light-sleep/  # C++20 app with ESP-IDF PM, tickless idle, and auto light sleep enabled
```

## Add To An ESP-IDF App

```cmake
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../esp32-aetus/components/aetus"
    "${CMAKE_CURRENT_LIST_DIR}/../esp32-aetus/components/nanopb"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(my_aetus_device)
```

Then depend on the component from your app component:

```cmake
idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES aetus
)
```

For C++ apps, register a `.cpp` source and enable C++20 for the component:

```cmake
idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES aetus
)

target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++20)
```

## Minimal Usage

```c
#include "aetus.h"

void app_main(void)
{
    aetus_config_t config = {
        .wifi_ssid = "ssidtest",
        .wifi_password = "********",
        .wifi_auth = AETUS_WIFI_AUTH_PSK,
        .ingest_url = "http://ingest.internal/v1/ingest",
        .time_url = "http://ingest.internal/v1/time",
        .device_id = "esp32c5-001",
        .device_token = "devtok_xxxxx",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 16,
        .connected_led_enabled = true,
        .connected_led_gpio = 27,
    };

    ESP_ERROR_CHECK(aetus_start(&config));
    ESP_ERROR_CHECK(aetus_sync_rtc(pdMS_TO_TICKS(30000)));

    aetus_telemetry_t telemetry;
    aetus_telemetry_init(&telemetry);
    ESP_ERROR_CHECK(aetus_telemetry_set_timestamp_rtc(&telemetry));
    ESP_ERROR_CHECK(aetus_telemetry_add_double(&telemetry, "temperature", 22.5, "celsius"));

    ESP_ERROR_CHECK(aetus_enqueue_telemetry(&telemetry, pdMS_TO_TICKS(1000)));
}
```

## NimBLE Provisioning

`aetus_start_provisioning()` starts a NimBLE GATT server that can receive Wi-Fi and upload configuration at runtime. The provisioning service keeps a pending config buffer; writes update the pending values, and writing any value to the `apply` characteristic calls `aetus_update_config()`.

When testing with generic tools such as nRF Connect, remember that writing `wifi_password` only updates the pending password. The device does not reconnect until the client also writes any value, for example `1`, to the `apply` characteristic.

The default provisioning advertiser uses a low-duty interval of about 3.5 seconds. After a central connects, AETUS automatically terminates the BLE connection after 10 minutes so forgotten provisioning sessions do not keep the radio active indefinitely.

```c
static void on_connection_check(
    uint16_t conn_handle,
    int status,
    uint16_t interval,
    uint16_t latency,
    uint16_t supervision_timeout,
    void *user_ctx
)
{
    (void)user_ctx;
    ESP_LOGI(
        "app",
        "BLE conn=%u status=%d interval=%u latency=%u timeout=%u",
        conn_handle,
        status,
        interval,
        latency,
        supervision_timeout
    );
}

void app_main(void)
{
    ESP_ERROR_CHECK(aetus_start(&config));

    const aetus_provisioning_config_t provisioning = {
        .device_name = "AETUS-C5",
        .config_changed_cb = NULL,
        .connection_check_cb = on_connection_check,
        .user_ctx = NULL,
    };
    ESP_ERROR_CHECK(aetus_start_provisioning(&provisioning));
}
```

The provisioning GATT service exposes these characteristics:

- `wifi_ssid`: read/write UTF-8 text, max 32 bytes.
- `wifi_auth`: read/write text, `psk` or `peap`.
- `wifi_id`: read/write text, used as PEAP identity and username, max 127 bytes.
- `wifi_password`: write-only text, max 64 bytes.
- `ingest_url`: read/write text, max 159 bytes.
- `time_url`: read/write text, max 159 bytes.
- `device_id`: read/write text, max 63 bytes.
- `device_token`: write-only text, max 127 bytes.
- `firmware_version`: read/write decimal integer.
- `upload_interval_ms`: read/write decimal integer.
- `queue_depth`: read/write decimal integer. This affects future config snapshots; the existing FreeRTOS queue is not resized after startup.
- `led_enabled`: read/write boolean text, `1`, `0`, `true`, `false`, `on`, or `off`.
- `led_gpio`: read/write decimal GPIO number.
- `apply`: write-only trigger; commits all pending values to the running AETUS stack.

Bluetooth SIG does not define adopted GATT characteristics for writing Wi-Fi credentials such as SSID, passphrase, or Wi-Fi auth mode. AETUS therefore uses vendor-specific 128-bit UUIDs for the provisioning service and its characteristics, rather than reusing unrelated adopted services such as Internet Protocol Support (`0x1820`) or Transport Discovery (`0x1824`).

Each characteristic also includes a `0x2901` Characteristic User Description descriptor with the same snake_case name, so tools such as nRF Connect can display readable labels instead of only raw UUIDs.

When the central updates BLE connection parameters, AETUS calls `connection_check_cb` with the current connection interval, latency, and supervision timeout. This gives the application a single hook for logging, policy checks, or disconnect decisions.

The connected LED is optional. If enabled, AETUS configures the selected GPIO as output, drives it high after `IP_EVENT_STA_GOT_IP`, and drives it low on Wi-Fi disconnect. The bundled `cpp-basic` example enables GPIO27 by default.

## WPA2-Enterprise PEAP

The stack also has a WPA2-Enterprise PEAP path for sites where the device must join Wi-Fi with `SSID + ID + password` instead of a pre-shared key.

This follows the ESP-IDF 6.0 `wifi_enterprise` example flow:

- Configure station SSID.
- Set PEAP identity.
- Set PEAP username and password.
- Restrict EAP method to `ESP_EAP_TYPE_PEAP`.
- Enable Wi-Fi Enterprise mode before `esp_wifi_start()`.

For the first implementation, AETUS intentionally keeps the public API simple and uses the same `id` value for both phase 1 identity and phase 2 username. If a site later requires anonymous outer identity plus a separate inner username, split `wifi_identity` into two fields.

```c
#include "aetus.h"

void app_main(void)
{
    aetus_config_t config = {
        .wifi_ssid = "enterprise-ssid",
        .wifi_password = "enterprise-password",
        .wifi_auth = AETUS_WIFI_AUTH_PEAP,
        .wifi_identity = "device-or-user-id",
        .ingest_url = "http://ingest.internal/v1/ingest",
        .time_url = "http://ingest.internal/v1/time",
        .device_id = "esp32c5-001",
        .device_token = "devtok_xxxxx",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 16,
    };

    ESP_ERROR_CHECK(aetus_start(&config));
}
```

```cpp
const aetus::Config config = aetus::Config()
                                 .wifi_peap("enterprise-ssid", "device-or-user-id", "enterprise-password")
                                 .ingest_url("http://ingest.internal/v1/ingest")
                                 .time_url("http://ingest.internal/v1/time")
                                 .device("esp32c5-001", "devtok_xxxxx")
                                 .firmware_version(1002003);
```

The local `cpp-basic` example can be configured for PEAP at build time:

```bash
export AETUS_WIFI_AUTH=peap
export AETUS_WIFI_SSID=enterprise-ssid
export AETUS_WIFI_ID=device-or-user-id
export AETUS_WIFI_PASSWORD=enterprise-password
idf.py -C firmware/esp32-aetus/examples/cpp-basic set-target esp32c5 reconfigure build
```

PEAP has not been live-tested in this repository yet because no Enterprise AP/RADIUS environment is currently available. Treat it as a compile-verified integration path until HIL coverage is added.

## Minimal C++ Usage

```cpp
#include <array>

#include "aetus.hpp"
#include "esp_check.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern "C" void app_main(void)
{
    const aetus::Config config = aetus::Config()
                                     .wifi("ssidtest", "********")
                                     .ingest_url("http://ingest.internal/v1/ingest")
                                     .time_url("http://ingest.internal/v1/time")
                                     .device("esp32c5-001", "devtok_xxxxx")
                                     .firmware_version(1002003)
                                     .upload_interval_ms(AETUS_UPLOAD_DEFAULT_INTERVAL_MS)
                                     .queue_depth(16)
                                     .connected_led(27);

    ESP_ERROR_CHECK(config.start());
    ESP_ERROR_CHECK(aetus_start_provisioning(nullptr));
    ESP_ERROR_CHECK(aetus::sync_rtc(pdMS_TO_TICKS(30000)));

    auto status = aetus::Status::online()
                      .reboot_reason("power_on")
                      .free_heap(esp_get_free_heap_size())
                      .timestamp_from_rtc();
    ESP_ERROR_CHECK(status.enqueue(pdMS_TO_TICKS(1000)));

    constexpr std::array<uint8_t, 4> raw_flags = {0xde, 0xad, 0xbe, 0xef};
    auto telemetry = aetus::Telemetry()
                         .timestamp_from_rtc()
                         .add_double("temperature", 22.5, "celsius")
                         .add_int64("battery_mv", 4012, "mV")
                         .add_bool("door_open", false)
                         .add_bytes("raw_flags", raw_flags);

    ESP_ERROR_CHECK(telemetry.enqueue(pdMS_TO_TICKS(1000)));
}
```

## API Contract

- `aetus_start()` must be called once before enqueue APIs.
- `aetus_sync_rtc()` performs authenticated `GET /v1/time` and sets the ESP32 RTC from `unix_time_ns`.
- `aetus_enqueue_telemetry()` and `aetus_enqueue_status()` are thread-safe from normal FreeRTOS task context.
- Enqueue APIs copy the message into the internal queue, so callers may reuse stack-local structs after the call returns.
- Enqueue APIs are not ISR-safe. Add `FromISR` variants later if an interrupt path needs direct event emission.
- `aetus_flush()` requests an immediate drain and waits for the uploader task to finish or timeout.

## Runtime Behavior

- Boot ID is generated once at startup as `boot-xxxxxxxx`.
- Sequence starts at `0` for each boot session.
- Sequence increments only after the server accepts an event.
- RTC timestamps are optional; call `aetus_sync_rtc()` first, then `aetus_telemetry_set_timestamp_rtc()` or `aetus_status_set_timestamp_rtc()`.
- Failed HTTP upload requeues the failed event to the front of the memory queue.
- The default upload interval is 10 minutes.
- The default transport is HTTP. HTTPS certificate policy is intentionally not part of this first portable component.

## Current Limits

- Queue persistence is in memory only.
- FlashDB durable backlog integration is the next firmware slice.
- The component owns Wi-Fi station startup. If an app already owns Wi-Fi, split the connectivity adapter before productizing.

## Current Consumer

`firmware/esp32c5-upload-smoke` is the HIL app that consumes this portable stack on a real ESP32-C5 board.

## Example Apps

This package includes local C++ examples:

- `firmware/esp32-aetus/examples/cpp-basic`: standalone ESP-IDF app using the C++20 wrapper, RTC sync, status event, telemetry metrics, and immediate flush.
- `firmware/esp32-aetus/examples/cpp-light-sleep`: standalone ESP-IDF app with `CONFIG_PM_ENABLE`, FreeRTOS tickless idle, Wi-Fi power save, and automatic light sleep enabled. It registers ESP-IDF light-sleep callbacks and logs `light_sleep_stats` so HIL runs can confirm that the idle windows are actually entering light sleep.

The example reads Wi-Fi/API credentials from environment variables at CMake configure time. For local HIL testing, keep those values in the untracked repository-level `.env.hil` file and build it with:

```bash
set -a
source .env.hil
set +a
source "$IDF_PATH/export.sh"
idf.py -C firmware/esp32-aetus/examples/cpp-basic set-target esp32c5 reconfigure build
```

Set `AETUS_WIFI_AUTH=peap` and `AETUS_WIFI_ID=<id>` to build the same example for WPA2-Enterprise PEAP. If `AETUS_WIFI_AUTH` is omitted, the example uses the normal PSK path. Use `reconfigure` after changing these environment variables because CMake does not always notice environment-only changes.

For the light-sleep example, use the same environment variables. `AETUS_UPLOAD_INTERVAL_MS` defaults to `60000` and `AETUS_SAMPLE_INTERVAL_MS` defaults to `30000` so the device has obvious idle windows between sample production and upload work.

```bash
set -a
source .env.hil
set +a
source "$IDF_PATH/export.sh"
idf.py -C firmware/esp32-aetus/examples/cpp-light-sleep set-target esp32c5 reconfigure build
idf.py -C firmware/esp32-aetus/examples/cpp-light-sleep -p /dev/cu.usbmodem1101 flash monitor
```

Expected boot logs include:

```text
auto light sleep enabled max_freq=240MHz min_freq=40MHz tickless=1
light sleep entry/exit callbacks registered
light_sleep_stats entries=<n> exits=<n> last_ms=<ms> total_ms=<ms>
```

On ESP32-C5 boards using the native USB-Serial/JTAG console, successful light sleep can disconnect the monitor with an error such as `Device not configured`. That is expected for low-power HIL runs unless the board is wired to a separate UART console or the application explicitly keeps USB awake. For current/power measurements, prefer an external meter and treat the callback counters as best-effort diagnostics when the console remains available.

The example intentionally does not start BLE provisioning by default. Continuous BLE advertising or a connected central can hold radio/PM locks and make light-sleep behavior harder to observe. Keep provisioning in `cpp-basic`, and use `cpp-light-sleep` when measuring idle power behavior.

The repository-level `firmware/examples` directory also contains standalone ESP-IDF apps that validate the intended developer experience:

- `basic-telemetry`: minimal boot/status and telemetry upload usage.
- `multitask-producers`: concurrent producer tasks calling the thread-safe enqueue API.
- `metric-types`: all supported protobuf metric value types.
- `cpp-friendly-interface`: C++20 wrapper usage from the repository-level examples area.

The examples target ESP32-C5, ESP-IDF 6.0, and a minimum 4MB external SPI flash with a 3MB factory app partition.
