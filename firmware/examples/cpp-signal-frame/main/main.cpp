#include <array>

#include "aetus.hpp"
#include "aetus_config.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "aetus_cpp_signal";

static void attach_rtc_timestamp_if_available(aetus::Status &status)
{
    const esp_err_t err = status.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "status timestamp skipped: %s", esp_err_to_name(err));
    }
}

static void attach_rtc_timestamp_if_available(aetus::SignalFrame &frame)
{
    const esp_err_t err = frame.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "signal frame timestamp skipped: %s", esp_err_to_name(err));
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
                                     .firmware_version(1002004)
                                     .upload_interval_ms(AETUS_UPLOAD_INTERVAL_MS)
                                     .queue_depth(16)
                                     .connected_led(27);

    ESP_LOGI(TAG, "starting C++ signal frame example ingest_url=%s interval_ms=%u", AETUS_INGEST_URL, AETUS_UPLOAD_INTERVAL_MS);
    ESP_ERROR_CHECK(config.start());

    const esp_err_t rtc_err = aetus::sync_rtc(pdMS_TO_TICKS(30000));
    if (rtc_err != ESP_OK) {
        ESP_LOGW(TAG, "RTC sync failed; signal frame may be uploaded without timestamp_ns: %s", esp_err_to_name(rtc_err));
    }

    auto status = aetus::Status::online()
                      .reboot_reason("signal_frame_start")
                      .rssi(-45)
                      .free_heap(esp_get_free_heap_size());
    attach_rtc_timestamp_if_available(status);
    ESP_ERROR_CHECK(status.enqueue(pdMS_TO_TICKS(1000)));

    constexpr std::array<float, 12> accel_samples = {
        0.10f, 0.20f, 9.81f,
        0.11f, 0.19f, 9.79f,
        0.13f, 0.18f, 9.82f,
        0.12f, 0.21f, 9.80f,
    };

    auto frame = aetus::SignalFrame()
                     .stream_key("cpp.accel.demo")
                     .sample_interval_ns(5'000'000ULL)
                     .sample_count(4)
                     .encoding_float32_le()
                     .layout_interleaved()
                     .add_channel("accel_x", "mps2")
                     .add_channel("accel_y", "mps2")
                     .add_channel("accel_z", "mps2")
                     .set_samples(std::span<const float>(accel_samples.data(), accel_samples.size()));
    attach_rtc_timestamp_if_available(frame);
    ESP_ERROR_CHECK(frame.enqueue(pdMS_TO_TICKS(1000)));

    ESP_ERROR_CHECK(aetus_flush(pdMS_TO_TICKS(60000)));
    ESP_LOGI(TAG, "queued and flushed signal frame event");
}
