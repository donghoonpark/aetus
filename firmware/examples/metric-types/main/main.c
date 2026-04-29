#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

#include "aetus.h"

static const char *TAG = "aetus_metric_types_example";

static void enqueue_all_metric_types(void)
{
    static const uint8_t sample_blob[] = {0xde, 0xad, 0xbe, 0xef};

    aetus_telemetry_t telemetry;
    aetus_telemetry_init(&telemetry);
    ESP_ERROR_CHECK(aetus_telemetry_add_int64(&telemetry, "event_count", 42, NULL));
    ESP_ERROR_CHECK(aetus_telemetry_add_double(&telemetry, "temperature", 24.125, "celsius"));
    ESP_ERROR_CHECK(aetus_telemetry_add_bool(&telemetry, "door_open", true, NULL));
    ESP_ERROR_CHECK(aetus_telemetry_add_string(&telemetry, "state", "measuring", NULL));
    ESP_ERROR_CHECK(aetus_telemetry_add_bytes(&telemetry, "raw_flags", sample_blob, sizeof(sample_blob), NULL));

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
