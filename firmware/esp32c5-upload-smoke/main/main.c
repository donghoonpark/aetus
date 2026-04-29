#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"
#include "aetus_config.h"

static const char *TAG = "aetus_smoke";

static void producer_task(void *arg)
{
    (void)arg;

    aetus_status_t status;
    aetus_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
    status.free_heap = esp_get_free_heap_size();
    if (aetus_status_set_timestamp_rtc(&status) != ESP_OK) {
        ESP_LOGW(TAG, "RTC timestamp unavailable for status");
    }
    ESP_ERROR_CHECK(aetus_status_set_reboot_reason(&status, "hil_smoke_start"));
    ESP_ERROR_CHECK(aetus_enqueue_status(&status, pdMS_TO_TICKS(1000)));
    ESP_LOGI(TAG, "queued startup status");

    for (int index = 0; index < 3; index++) {
        aetus_telemetry_t message;
        aetus_telemetry_init(&message);
        if (aetus_telemetry_set_timestamp_rtc(&message) != ESP_OK) {
            ESP_LOGW(TAG, "RTC timestamp unavailable for telemetry index=%d", index);
        }
        ESP_ERROR_CHECK(aetus_telemetry_add_double(&message, "temperature", 22.25 + index, "celsius"));
        ESP_ERROR_CHECK(aetus_telemetry_add_int64(&message, "battery_mv", 4012 - (index * 3), "mV"));
        ESP_ERROR_CHECK(aetus_telemetry_add_bool(&message, "motion_detected", (index % 2) == 0, ""));
        ESP_ERROR_CHECK(aetus_telemetry_add_string(&message, "sample_note", "esp32c5-hil", ""));

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
        .time_url = AETUS_TIME_URL,
        .device_id = AETUS_DEVICE_ID,
        .device_token = AETUS_DEVICE_TOKEN,
        .firmware_version = 1002003,
        .upload_interval_ms = AETUS_UPLOAD_INTERVAL_MS,
        .queue_depth = 16,
    };

    ESP_LOGI(TAG, "starting AETUS ESP32-C5 upload smoke");
    ESP_LOGI(TAG, "ingest_url=%s interval_ms=%u", config.ingest_url, (unsigned)config.upload_interval_ms);
    ESP_ERROR_CHECK(aetus_start(&config));
    ESP_ERROR_CHECK(aetus_sync_rtc(pdMS_TO_TICKS(30000)));

    xTaskCreate(producer_task, "aetus_producer", 4096, NULL, 4, NULL);
}
