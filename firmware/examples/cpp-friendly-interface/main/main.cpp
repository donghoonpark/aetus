#include <array>

#include "aetus.hpp"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "aetus_cpp_example";

#ifdef CONFIG_AETUS_ISR_SAFE_ENQUEUE
struct IsrExampleItems {
    const aetus::Status *status;
    const aetus::Telemetry *telemetry;
};

static bool IRAM_ATTR example_isr_callback(void *user_ctx)
{
    const auto *items = static_cast<const IsrExampleItems *>(user_ctx);
    if (items == nullptr || items->status == nullptr || items->telemetry == nullptr) {
        return false;
    }

    BaseType_t woken = pdFALSE;

    (void)items->status->enqueue_from_isr(&woken);
    (void)items->telemetry->enqueue_from_isr(&woken);
    return woken != pdFALSE;
}
#endif

static void try_attach_rtc_timestamp(aetus::Telemetry &telemetry)
{
    const esp_err_t err = telemetry.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "RTC timestamp unavailable; uploading without timestamp_ns: %s", esp_err_to_name(err));
    }
}

static void try_attach_rtc_timestamp(aetus::Status &status)
{
    const esp_err_t err = status.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "RTC timestamp unavailable; uploading without timestamp_ns: %s", esp_err_to_name(err));
    }
}

extern "C" void app_main(void)
{
    const aetus::Config config = aetus::Config()
                                     .wifi("CHANGE_ME", "CHANGE_ME")
                                     .ingest_url("http://ingest.internal/v1/ingest")
                                     .device("esp32c5-example-cpp", "devtok_example")
                                     .firmware_version(1002003)
                                     .upload_interval_ms(AETUS_UPLOAD_DEFAULT_INTERVAL_MS)
                                     .queue_depth(16);

    ESP_ERROR_CHECK(config.start());

    auto status = aetus::Status::online()
                      .reboot_reason("power_on")
                      .rssi(-45)
                      .free_heap(esp_get_free_heap_size());
    try_attach_rtc_timestamp(status);
    ESP_ERROR_CHECK(status.enqueue(pdMS_TO_TICKS(1000)));

    constexpr std::array<uint8_t, 4> raw_flags = {0xde, 0xad, 0xbe, 0xef};
    aetus::Telemetry telemetry;
    telemetry.add_double("temperature", 23.75, "celsius")
        .add_int64("battery_mv", 4012, "mV")
        .add_bool("door_open", false)
        .add_string("state", "measuring")
        .add_bytes("raw_flags", raw_flags);
    try_attach_rtc_timestamp(telemetry);
    ESP_ERROR_CHECK(telemetry.enqueue(pdMS_TO_TICKS(1000)));

    ESP_LOGI(TAG, "queued C++ status and telemetry payloads");
}
