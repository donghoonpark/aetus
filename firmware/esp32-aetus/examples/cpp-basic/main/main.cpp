#include <array>

#include "aetus.hpp"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "aetus_cpp_basic";

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
                                     .wifi("CHANGE_ME", "CHANGE_ME")
                                     .ingest_url("http://ingest.internal/v1/ingest")
                                     .time_url("http://ingest.internal/v1/time")
                                     .device("esp32c5-cpp-basic", "devtok_example")
                                     .firmware_version(1002003)
                                     .upload_interval_ms(AETUS_UPLOAD_DEFAULT_INTERVAL_MS)
                                     .queue_depth(16);

    ESP_ERROR_CHECK(config.start());

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
