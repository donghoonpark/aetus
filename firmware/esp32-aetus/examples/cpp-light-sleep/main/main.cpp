#include <atomic>
#include <cstdint>

#include "aetus.hpp"
#include "aetus_config.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "aetus_cpp_light_sleep";

#if !CONFIG_PM_ENABLE
#error "cpp-light-sleep requires CONFIG_PM_ENABLE=y"
#endif

#if !CONFIG_FREERTOS_USE_TICKLESS_IDLE
#error "cpp-light-sleep requires CONFIG_FREERTOS_USE_TICKLESS_IDLE=y"
#endif

static std::atomic<uint32_t> s_light_sleep_entries{0};
static std::atomic<uint32_t> s_light_sleep_exits{0};
static std::atomic<uint32_t> s_last_light_sleep_ms{0};
static std::atomic<uint32_t> s_total_light_sleep_ms{0};
static std::atomic<uint32_t> s_sample_index{0};

static uint32_t clamp_ms(int64_t sleep_time_us)
{
    if (sleep_time_us <= 0) {
        return 0;
    }
    const uint64_t sleep_ms = static_cast<uint64_t>(sleep_time_us) / 1000ULL;
    return sleep_ms > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(sleep_ms);
}

#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS
static esp_err_t on_light_sleep_enter(int64_t sleep_time_us, void *arg)
{
    (void)sleep_time_us;
    (void)arg;
    s_light_sleep_entries.fetch_add(1, std::memory_order_relaxed);
    return ESP_OK;
}

static esp_err_t on_light_sleep_exit(int64_t sleep_time_us, void *arg)
{
    (void)arg;
    const uint32_t slept_ms = clamp_ms(sleep_time_us);
    s_light_sleep_exits.fetch_add(1, std::memory_order_relaxed);
    s_last_light_sleep_ms.store(slept_ms, std::memory_order_relaxed);
    s_total_light_sleep_ms.fetch_add(slept_ms, std::memory_order_relaxed);
    return ESP_OK;
}
#endif

static void enable_auto_light_sleep()
{
    const esp_pm_config_t pm_config = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = 40,
        .light_sleep_enable = true,
    };
    ESP_ERROR_CHECK(esp_pm_configure(&pm_config));
    ESP_LOGI(
        TAG,
        "auto light sleep enabled max_freq=%dMHz min_freq=%dMHz tickless=%d",
        pm_config.max_freq_mhz,
        pm_config.min_freq_mhz,
        CONFIG_FREERTOS_USE_TICKLESS_IDLE
    );

#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS
    static esp_pm_sleep_cbs_register_config_t callbacks = {
        .enter_cb = on_light_sleep_enter,
        .exit_cb = on_light_sleep_exit,
        .enter_cb_user_arg = nullptr,
        .exit_cb_user_arg = nullptr,
        .enter_cb_prior = 0,
        .exit_cb_prior = 0,
    };
    ESP_ERROR_CHECK(esp_pm_light_sleep_register_cbs(&callbacks));
    ESP_LOGI(TAG, "light sleep entry/exit callbacks registered");
#endif
}

static void enable_wifi_power_save_if_available()
{
    const esp_err_t err = esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "wifi power save enabled: WIFI_PS_MIN_MODEM");
        return;
    }
    ESP_LOGW(TAG, "wifi power save not applied yet: %s", esp_err_to_name(err));
}

static void try_attach_rtc_timestamp(aetus::Telemetry &telemetry)
{
    const esp_err_t err = telemetry.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "telemetry timestamp skipped: %s", esp_err_to_name(err));
    }
}

static void try_attach_rtc_timestamp(aetus::Status &status)
{
    const esp_err_t err = status.try_timestamp_from_rtc();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "status timestamp skipped: %s", esp_err_to_name(err));
    }
}

static void enqueue_boot_status()
{
    auto status = aetus::Status::online()
                      .reboot_reason("power_on")
                      .free_heap(esp_get_free_heap_size());
    try_attach_rtc_timestamp(status);
    ESP_ERROR_CHECK(status.enqueue(pdMS_TO_TICKS(1000)));
}

static void producer_task(void *arg)
{
    (void)arg;
    while (true) {
        const uint32_t index = s_sample_index.fetch_add(1, std::memory_order_relaxed);
        auto telemetry = aetus::Telemetry()
                             .add_int64("sample_index", index)
                             .add_int64("uptime_ms", esp_timer_get_time() / 1000, "ms")
                             .add_int64("free_heap", esp_get_free_heap_size(), "bytes")
                             .add_int64("sleep_entries", s_light_sleep_entries.load(std::memory_order_relaxed))
                             .add_int64("last_sleep_ms", s_last_light_sleep_ms.load(std::memory_order_relaxed), "ms");
        try_attach_rtc_timestamp(telemetry);

        const esp_err_t err = telemetry.enqueue(pdMS_TO_TICKS(1000));
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "queued sample index=%lu", static_cast<unsigned long>(index));
        } else {
            ESP_LOGW(TAG, "sample enqueue failed: %s", esp_err_to_name(err));
        }
        vTaskDelay(pdMS_TO_TICKS(AETUS_SAMPLE_INTERVAL_MS));
    }
}

static void sleep_reporter_task(void *arg)
{
    (void)arg;
    while (true) {
        ESP_LOGI(
            TAG,
            "light_sleep_stats entries=%lu exits=%lu last_ms=%lu total_ms=%lu",
            static_cast<unsigned long>(s_light_sleep_entries.load(std::memory_order_relaxed)),
            static_cast<unsigned long>(s_light_sleep_exits.load(std::memory_order_relaxed)),
            static_cast<unsigned long>(s_last_light_sleep_ms.load(std::memory_order_relaxed)),
            static_cast<unsigned long>(s_total_light_sleep_ms.load(std::memory_order_relaxed))
        );
        vTaskDelay(pdMS_TO_TICKS(30000));
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
                                     .firmware_version(1002003)
                                     .upload_interval_ms(AETUS_UPLOAD_INTERVAL_MS)
                                     .queue_depth(16)
                                     .connected_led(27);

    ESP_LOGI(
        TAG,
        "starting light-sleep example ingest_url=%s upload_interval_ms=%u sample_interval_ms=%u",
        AETUS_INGEST_URL,
        AETUS_UPLOAD_INTERVAL_MS,
        AETUS_SAMPLE_INTERVAL_MS
    );
    ESP_ERROR_CHECK(config.start());

    const esp_err_t rtc_err = aetus::sync_rtc(pdMS_TO_TICKS(30000));
    if (rtc_err != ESP_OK) {
        ESP_LOGW(TAG, "RTC sync failed; events may be uploaded without timestamp_ns: %s", esp_err_to_name(rtc_err));
    }
    enable_wifi_power_save_if_available();

    enqueue_boot_status();

    const esp_err_t flush_err = aetus_flush(pdMS_TO_TICKS(60000));
    if (flush_err == ESP_OK) {
        ESP_LOGI(TAG, "boot status flushed");
    } else {
        ESP_LOGW(TAG, "boot status flush failed; continuing light-sleep demo: %s", esp_err_to_name(flush_err));
    }

    enable_auto_light_sleep();
    ESP_LOGI(TAG, "idle windows should now enter automatic light sleep");

    xTaskCreate(producer_task, "aetus_sample_producer", 4096, nullptr, 4, nullptr);
    xTaskCreate(sleep_reporter_task, "aetus_sleep_reporter", 4096, nullptr, 3, nullptr);
}
