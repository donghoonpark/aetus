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

## Minimal Usage

```c
#include "aetus.h"

void app_main(void)
{
    aetus_config_t config = {
        .wifi_ssid = "SilverPark5G",
        .wifi_password = "********",
        .ingest_url = "http://ingest.internal/v1/ingest",
        .device_id = "esp32c5-001",
        .device_token = "devtok_xxxxx",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 16,
    };

    ESP_ERROR_CHECK(aetus_start(&config));

    aetus_telemetry_t telemetry = {0};
    telemetry.metric_count = 1;
    strncpy(telemetry.metrics[0].key, "temperature", sizeof(telemetry.metrics[0].key) - 1);
    telemetry.metrics[0].type = AETUS_METRIC_VALUE_DOUBLE;
    telemetry.metrics[0].value.double_value = 22.5;
    strncpy(telemetry.metrics[0].unit, "celsius", sizeof(telemetry.metrics[0].unit) - 1);

    ESP_ERROR_CHECK(aetus_enqueue_telemetry(&telemetry, pdMS_TO_TICKS(1000)));
}
```

## API Contract

- `aetus_start()` must be called once before enqueue APIs.
- `aetus_enqueue_telemetry()` and `aetus_enqueue_status()` are thread-safe from normal FreeRTOS task context.
- Enqueue APIs copy the message into the internal queue, so callers may reuse stack-local structs after the call returns.
- Enqueue APIs are not ISR-safe. Add `FromISR` variants later if an interrupt path needs direct event emission.
- `aetus_flush()` requests an immediate drain and waits for the uploader task to finish or timeout.

## Runtime Behavior

- Boot ID is generated once at startup as `boot-xxxxxxxx`.
- Sequence starts at `0` for each boot session.
- Sequence increments only after the server accepts an event.
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

`firmware/examples` contains standalone ESP-IDF apps that validate the intended developer experience:

- `basic-telemetry`: minimal boot/status and telemetry upload usage.
- `multitask-producers`: concurrent producer tasks calling the thread-safe enqueue API.
- `metric-types`: all supported protobuf metric value types.

The examples target ESP32-C5, ESP-IDF 6.0, and a minimum 4MB external SPI flash with a 3MB factory app partition.
