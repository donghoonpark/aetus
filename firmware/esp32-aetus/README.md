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
    cpp-basic/  # Standalone ESP-IDF app showing the C++20 wrapper API
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
        .ingest_url = "http://ingest.internal/v1/ingest",
        .time_url = "http://ingest.internal/v1/time",
        .device_id = "esp32c5-001",
        .device_token = "devtok_xxxxx",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 16,
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
                                     .queue_depth(16);

    ESP_ERROR_CHECK(config.start());
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
- NimBLE provisioning or diagnostics is not included in this component yet.
- The component owns Wi-Fi station startup. If an app already owns Wi-Fi, split the connectivity adapter before productizing.

## Current Consumer

`firmware/esp32c5-upload-smoke` is the HIL app that consumes this portable stack on a real ESP32-C5 board.

## Example Apps

This package includes a local C++ example:

- `firmware/esp32-aetus/examples/cpp-basic`: standalone ESP-IDF app using the C++20 wrapper, RTC sync, status event, telemetry metrics, and immediate flush.

Build it with:

```bash
source "$IDF_PATH/export.sh"
idf.py -C firmware/esp32-aetus/examples/cpp-basic set-target esp32c5 build
```

The repository-level `firmware/examples` directory also contains standalone ESP-IDF apps that validate the intended developer experience:

- `basic-telemetry`: minimal boot/status and telemetry upload usage.
- `multitask-producers`: concurrent producer tasks calling the thread-safe enqueue API.
- `metric-types`: all supported protobuf metric value types.
- `cpp-friendly-interface`: C++20 wrapper usage from the repository-level examples area.

The examples target ESP32-C5, ESP-IDF 6.0, and a minimum 4MB external SPI flash with a 3MB factory app partition.
