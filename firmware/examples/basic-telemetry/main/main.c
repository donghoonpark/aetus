#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"

static const char *TAG = "aetus_basic_example";

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }
    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static void enqueue_example_payloads(void)
{
    aetus_status_t status = {
        .status = AETUS_DEVICE_STATUS_ONLINE,
        .rssi = -45,
        .free_heap = 0,
        .timestamp_ns = 0,
    };
    copy_string(status.reboot_reason, sizeof(status.reboot_reason), "power_on");
    ESP_ERROR_CHECK(aetus_enqueue_status(&status, pdMS_TO_TICKS(1000)));

    aetus_telemetry_t telemetry = {0};
    telemetry.metric_count = 2;

    copy_string(telemetry.metrics[0].key, sizeof(telemetry.metrics[0].key), "temperature");
    telemetry.metrics[0].type = AETUS_METRIC_VALUE_DOUBLE;
    telemetry.metrics[0].value.double_value = 23.75;
    copy_string(telemetry.metrics[0].unit, sizeof(telemetry.metrics[0].unit), "celsius");

    copy_string(telemetry.metrics[1].key, sizeof(telemetry.metrics[1].key), "battery_mv");
    telemetry.metrics[1].type = AETUS_METRIC_VALUE_INT64;
    telemetry.metrics[1].value.int64_value = 4012;
    copy_string(telemetry.metrics[1].unit, sizeof(telemetry.metrics[1].unit), "mV");

    ESP_ERROR_CHECK(aetus_enqueue_telemetry(&telemetry, pdMS_TO_TICKS(1000)));
    ESP_LOGI(TAG, "queued basic status and telemetry payloads");
}

void app_main(void)
{
    const aetus_config_t config = {
        .wifi_ssid = "CHANGE_ME",
        .wifi_password = "CHANGE_ME",
        .ingest_url = "http://ingest.internal/v1/ingest",
        .device_id = "esp32c5-example-basic",
        .device_token = "devtok_example",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 8,
    };

    ESP_ERROR_CHECK(aetus_start(&config));
    enqueue_example_payloads();
}
