#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"
#include "aetus_config.h"

static const char *TAG = "aetus_smoke";

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }

    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static void producer_task(void *arg)
{
    (void)arg;

    aetus_status_t status = {
        .status = AETUS_DEVICE_STATUS_ONLINE,
        .rssi = 0,
        .free_heap = 0,
        .timestamp_ns = 0,
    };
    copy_string(status.reboot_reason, sizeof(status.reboot_reason), "hil_smoke_start");
    ESP_ERROR_CHECK(aetus_enqueue_status(&status, pdMS_TO_TICKS(1000)));
    ESP_LOGI(TAG, "queued startup status");

    for (int index = 0; index < 3; index++) {
        aetus_telemetry_t message = {0};
        message.metric_count = 4;
        copy_string(message.metrics[0].key, sizeof(message.metrics[0].key), "temperature");
        message.metrics[0].type = AETUS_METRIC_VALUE_DOUBLE;
        message.metrics[0].value.double_value = 22.25 + index;
        copy_string(message.metrics[0].unit, sizeof(message.metrics[0].unit), "celsius");

        copy_string(message.metrics[1].key, sizeof(message.metrics[1].key), "battery_mv");
        message.metrics[1].type = AETUS_METRIC_VALUE_INT64;
        message.metrics[1].value.int64_value = 4012 - (index * 3);
        copy_string(message.metrics[1].unit, sizeof(message.metrics[1].unit), "mV");

        copy_string(message.metrics[2].key, sizeof(message.metrics[2].key), "motion_detected");
        message.metrics[2].type = AETUS_METRIC_VALUE_BOOL;
        message.metrics[2].value.bool_value = (index % 2) == 0;

        copy_string(message.metrics[3].key, sizeof(message.metrics[3].key), "sample_note");
        message.metrics[3].type = AETUS_METRIC_VALUE_STRING;
        copy_string(message.metrics[3].value.string_value, sizeof(message.metrics[3].value.string_value), "esp32c5-hil");

        ESP_ERROR_CHECK(aetus_enqueue_telemetry(&message, pdMS_TO_TICKS(1000)));
        ESP_LOGI(TAG, "queued smoke message index=%d", index);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    esp_err_t flush_err = aetus_flush(pdMS_TO_TICKS(60000));
    if (flush_err != ESP_OK) {
        ESP_LOGW(TAG, "manual upload flush did not complete: %s", esp_err_to_name(flush_err));
    }

    ESP_LOGI(TAG, "smoke producer finished");
    vTaskDelete(NULL);
}

void app_main(void)
{
    aetus_config_t config = {
        .wifi_ssid = AETUS_WIFI_SSID,
        .wifi_password = AETUS_WIFI_PASSWORD,
        .ingest_url = AETUS_INGEST_URL,
        .device_id = AETUS_DEVICE_ID,
        .device_token = AETUS_DEVICE_TOKEN,
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_INTERVAL_MS,
        .queue_depth = 16,
    };

    ESP_LOGI(TAG, "starting AETUS ESP32-C5 upload smoke");
    ESP_LOGI(TAG, "ingest_url=%s interval_ms=%u", config.ingest_url, (unsigned)config.upload_interval_ms);
    ESP_ERROR_CHECK(aetus_start(&config));

    xTaskCreate(producer_task, "aetus_producer", 4096, NULL, 4, NULL);
}
