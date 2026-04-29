#include <stdio.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"

static const char *TAG = "aetus_multitask_example";

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }
    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static void sensor_producer_task(void *arg)
{
    const char *sensor_name = (const char *)arg;

    for (int index = 0; index < 5; index++) {
        aetus_telemetry_t telemetry = {0};
        telemetry.metric_count = 2;

        copy_string(telemetry.metrics[0].key, sizeof(telemetry.metrics[0].key), sensor_name);
        telemetry.metrics[0].type = AETUS_METRIC_VALUE_DOUBLE;
        telemetry.metrics[0].value.double_value = 10.0 + index;
        copy_string(telemetry.metrics[0].unit, sizeof(telemetry.metrics[0].unit), "raw");

        copy_string(telemetry.metrics[1].key, sizeof(telemetry.metrics[1].key), "producer_index");
        telemetry.metrics[1].type = AETUS_METRIC_VALUE_INT64;
        telemetry.metrics[1].value.int64_value = index;

        esp_err_t err = aetus_enqueue_telemetry(&telemetry, pdMS_TO_TICKS(100));
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "queue full for %s index=%d err=%s", sensor_name, index, esp_err_to_name(err));
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }

    ESP_LOGI(TAG, "%s producer finished", sensor_name);
    vTaskDelete(NULL);
}

void app_main(void)
{
    const aetus_config_t config = {
        .wifi_ssid = "CHANGE_ME",
        .wifi_password = "CHANGE_ME",
        .ingest_url = "http://ingest.internal/v1/ingest",
        .device_id = "esp32c5-example-multitask",
        .device_token = "devtok_example",
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS,
        .queue_depth = 16,
    };

    ESP_ERROR_CHECK(aetus_start(&config));
    xTaskCreate(sensor_producer_task, "aetus_temp_prod", 4096, "temperature", 4, NULL);
    xTaskCreate(sensor_producer_task, "aetus_light_prod", 4096, "ambient_lux", 4, NULL);
}
