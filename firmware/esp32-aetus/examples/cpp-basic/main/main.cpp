#include <array>

#include "aetus.hpp"
#include "aetus_config.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "aetus_cpp_basic";

static void on_provisioning_connection_check(
    uint16_t conn_handle,
    int status,
    uint16_t interval,
    uint16_t latency,
    uint16_t supervision_timeout,
    void *user_ctx
)
{
    (void)user_ctx;
    ESP_LOGI(
        TAG,
        "provisioning link conn=%u status=%d interval=%u latency=%u timeout=%u",
        conn_handle,
        status,
        interval,
        latency,
        supervision_timeout
    );
}

static void attach_rtc_timestamp_if_available(aetus::Status &status)
{
    const esp_err_t err = status.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "status timestamp skipped: %s", esp_err_to_name(err));
    }
}

static void attach_rtc_timestamp_if_available(aetus::Telemetry &telemetry)
{
    const esp_err_t err = telemetry.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "telemetry timestamp skipped: %s", esp_err_to_name(err));
    }
}

extern "C" void app_main(void)
{
    const aetus::Config config = aetus::Config()
#if AETUS_WIFI_AUTH_PEAP
                                     .wifi_peap(AETUS_WIFI_SSID, AETUS_WIFI_ID, AETUS_WIFI_PASSWORD)
#else
                                     .wifi(AETUS_WIFI_SSID, AETUS_WIFI_PASSWORD)
#endif
                                     .ingest_url(AETUS_INGEST_URL)
                                     .time_url(AETUS_TIME_URL)
                                     .device(AETUS_DEVICE_ID, AETUS_DEVICE_TOKEN)
#if AETUS_AUTH_HMAC
                                     .hmac_sha256_auth()
#else
                                     .bearer_auth()
#endif
                                     .firmware_version(1002003)
                                     .upload_interval_ms(AETUS_UPLOAD_INTERVAL_MS)
                                     .queue_depth(16)
                                     .connected_led(27);

    ESP_LOGI(TAG, "starting C++ example ingest_url=%s interval_ms=%u", AETUS_INGEST_URL, AETUS_UPLOAD_INTERVAL_MS);
    ESP_ERROR_CHECK(config.start());

    const aetus_provisioning_config_t provisioning_config = {
        .device_name = "AETUS-C5",
        .config_changed_cb = nullptr,
        .connection_check_cb = on_provisioning_connection_check,
        .user_ctx = nullptr,
    };
    ESP_ERROR_CHECK(aetus_start_provisioning(&provisioning_config));

    const esp_err_t rtc_err = aetus::sync_rtc(pdMS_TO_TICKS(30000));
    if (rtc_err != ESP_OK) {
        ESP_LOGW(TAG, "RTC sync failed; events will be uploaded without timestamp_ns: %s", esp_err_to_name(rtc_err));
    }

    auto status = aetus::Status::online()
                      .reboot_reason("power_on")
                      .rssi(-48)
                      .free_heap(esp_get_free_heap_size());
    attach_rtc_timestamp_if_available(status);
    ESP_ERROR_CHECK(status.enqueue(pdMS_TO_TICKS(1000)));

    constexpr std::array<uint8_t, 4> raw_flags = {0xde, 0xad, 0xbe, 0xef};
    auto telemetry = aetus::Telemetry()
                         .add_double("temperature", 23.75, "celsius")
                         .add_int64("battery_mv", 4012, "mV")
                         .add_bool("door_open", false)
                         .add_string("state", "measuring")
                         .add_bytes("raw_flags", raw_flags);
    attach_rtc_timestamp_if_available(telemetry);
    ESP_ERROR_CHECK(telemetry.enqueue(pdMS_TO_TICKS(1000)));

    ESP_ERROR_CHECK(aetus_flush(pdMS_TO_TICKS(60000)));
    ESP_LOGI(TAG, "queued and flushed C++ status and telemetry events");
}
