#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"

static const char *TAG = "aetus_basic_example";

static void enqueue_example_payloads(void)
{
    aetus_status_t status;
    aetus_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
    status.rssi = -45;
    ESP_ERROR_CHECK(aetus_status_set_reboot_reason(&status, "power_on"));
    ESP_ERROR_CHECK(aetus_enqueue_status(&status, pdMS_TO_TICKS(1000)));

    aetus_telemetry_t telemetry;
    aetus_telemetry_init(&telemetry);
    ESP_ERROR_CHECK(aetus_telemetry_add_double(&telemetry, "temperature", 23.75, "celsius"));
    ESP_ERROR_CHECK(aetus_telemetry_add_int64(&telemetry, "battery_mv", 4012, "mV"));
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
