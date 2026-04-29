#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus_config.h"
#include "aetus_uploader.h"

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

    for (int index = 0; index < 3; index++) {
        aetus_upload_message_t message = {0};
        message.metric_count = 2;
        copy_string(message.metrics[0].key, sizeof(message.metrics[0].key), "temperature");
        message.metrics[0].value = 22.25 + index;
        copy_string(message.metrics[0].unit, sizeof(message.metrics[0].unit), "celsius");
        copy_string(message.metrics[1].key, sizeof(message.metrics[1].key), "battery_mv");
        message.metrics[1].value = 4012.0 - (index * 3.0);
        copy_string(message.metrics[1].unit, sizeof(message.metrics[1].unit), "mV");

        ESP_ERROR_CHECK(aetus_uploader_enqueue(&message, pdMS_TO_TICKS(1000)));
        ESP_LOGI(TAG, "queued smoke message index=%d", index);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    ESP_LOGI(TAG, "smoke producer finished; waiting for upload timer");
    vTaskDelete(NULL);
}

void app_main(void)
{
    aetus_uploader_config_t config = {
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
    ESP_ERROR_CHECK(aetus_uploader_start(&config));
    ESP_ERROR_CHECK(aetus_uploader_flush(pdMS_TO_TICKS(10)));

    xTaskCreate(producer_task, "aetus_producer", 4096, NULL, 4, NULL);
}
