#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

#include "aetus.h"

static const char *TAG = "aetus_metric_types_example";

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }
    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static void enqueue_all_metric_types(void)
{
    static const uint8_t sample_blob[] = {0xde, 0xad, 0xbe, 0xef};

    aetus_telemetry_t telemetry = {0};
    telemetry.metric_count = 5;

    copy_string(telemetry.metrics[0].key, sizeof(telemetry.metrics[0].key), "event_count");
    telemetry.metrics[0].type = AETUS_METRIC_VALUE_INT64;
    telemetry.metrics[0].value.int64_value = 42;

    copy_string(telemetry.metrics[1].key, sizeof(telemetry.metrics[1].key), "temperature");
    telemetry.metrics[1].type = AETUS_METRIC_VALUE_DOUBLE;
    telemetry.metrics[1].value.double_value = 24.125;
    copy_string(telemetry.metrics[1].unit, sizeof(telemetry.metrics[1].unit), "celsius");

    copy_string(telemetry.metrics[2].key, sizeof(telemetry.metrics[2].key), "door_open");
    telemetry.metrics[2].type = AETUS_METRIC_VALUE_BOOL;
    telemetry.metrics[2].value.bool_value = true;

    copy_string(telemetry.metrics[3].key, sizeof(telemetry.metrics[3].key), "state");
    telemetry.metrics[3].type = AETUS_METRIC_VALUE_STRING;
    copy_string(telemetry.metrics[3].value.string_value, sizeof(telemetry.metrics[3].value.string_value), "measuring");

    copy_string(telemetry.metrics[4].key, sizeof(telemetry.metrics[4].key), "raw_flags");
    telemetry.metrics[4].type = AETUS_METRIC_VALUE_BYTES;
    memcpy(telemetry.metrics[4].value.bytes_value.data, sample_blob, sizeof(sample_blob));
    telemetry.metrics[4].value.bytes_value.size = sizeof(sample_blob);

    ESP_ERROR_CHECK(aetus_enqueue_telemetry(&telemetry, pdMS_TO_TICKS(1000)));
    ESP_LOGI(TAG, "queued all supported metric value types");
}

void app_main(void)
{
    const aetus_config_t config = {
        .wifi_ssid = "CHANGE_ME",
        .wifi_password = "CHANGE_ME",
        .ingest_url = "http://ingest.internal/v1/ingest",
        .device_id = "esp32c5-example-metric-types",
        .device_token = "devtok_example",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 8,
    };

    ESP_ERROR_CHECK(aetus_start(&config));
    enqueue_all_metric_types();
}
