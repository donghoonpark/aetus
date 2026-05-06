#include "driver/gptimer.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "aetus.h"
#include "aetus_config.h"

static const char *TAG = "isr_enqueue_hil";

static volatile int isr_fire_count = 0;
static volatile bool isr_telemetry_ok = false;
static volatile bool isr_status_ok = false;
static volatile bool isr_overflow_rejected = false;
static volatile BaseType_t isr_woken = pdFALSE;

static bool IRAM_ATTR isr_timer_callback(
    gptimer_handle_t timer,
    const gptimer_alarm_event_data_t *edata,
    void *user_ctx
)
{
    (void)timer;
    (void)edata;
    (void)user_ctx;

    BaseType_t pxHigherPriorityTaskWoken = pdFALSE;

#ifdef CONFIG_AETUS_ISR_SAFE_ENQUEUE
    aetus_telemetry_t telemetry;
    aetus_telemetry_init(&telemetry);
    aetus_telemetry_add_double(&telemetry, "temperature", 23.75, "celsius");
    aetus_telemetry_add_int64(&telemetry, "battery_mv", 3950, "mV");

    esp_err_t telemetry_err = aetus_enqueue_telemetry_from_isr(&telemetry, &pxHigherPriorityTaskWoken);

    aetus_telemetry_t overflow;
    aetus_telemetry_init(&overflow);
    overflow.metric_count = AETUS_TELEMETRY_INLINE_METRICS + 1;
    esp_err_t overflow_err = aetus_enqueue_telemetry_from_isr(&overflow, &pxHigherPriorityTaskWoken);

    aetus_status_t status;
    aetus_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
    aetus_status_set_reboot_reason(&status, "isr_hil_start");

    esp_err_t status_err = aetus_enqueue_status_from_isr(&status, &pxHigherPriorityTaskWoken);

    isr_telemetry_ok = (telemetry_err == ESP_OK);
    isr_status_ok = (status_err == ESP_OK);
    isr_overflow_rejected = (overflow_err == ESP_ERR_INVALID_ARG);
#else
    isr_telemetry_ok = false;
    isr_status_ok = false;
    isr_overflow_rejected = false;
#endif
    isr_fire_count++;
    isr_woken = pxHigherPriorityTaskWoken;

    return pxHigherPriorityTaskWoken != pdFALSE;
}

void app_main(void)
{
    UBaseType_t initial_hwm = uxTaskGetStackHighWaterMark(NULL);
    ESP_LOGI(TAG, "ISR enqueue HIL test starting. initial_hwm=%lu", (unsigned long)initial_hwm);

    aetus_config_t config = {
        .wifi_ssid = AETUS_WIFI_SSID,
        .wifi_password = AETUS_WIFI_PASSWORD,
        .ingest_url = AETUS_INGEST_URL,
        .time_url = AETUS_TIME_URL,
        .device_id = AETUS_DEVICE_ID,
        .device_token = AETUS_DEVICE_TOKEN,
        .auth_mode = AETUS_AUTH_HMAC ? AETUS_AUTH_HMAC_SHA256 : AETUS_AUTH_BEARER,
        .firmware_version = 1002003,
        .upload_interval_ms = 60000,
        .queue_depth = 16,
    };

    ESP_LOGI(TAG, "starting AETUS with ingest_url=%s device=%s", config.ingest_url, config.device_id);
    ESP_ERROR_CHECK(aetus_start(&config));

    esp_err_t rtc_err = aetus_sync_rtc(pdMS_TO_TICKS(30000));
    if (rtc_err != ESP_OK) {
        ESP_LOGW(TAG, "RTC sync failed (non-fatal): %s", esp_err_to_name(rtc_err));
    }

    ESP_LOGI(TAG, "setting up gptimer for ISR enqueue test");

    gptimer_handle_t gptimer = NULL;
    gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1 * 1000 * 1000,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&timer_config, &gptimer));

    gptimer_alarm_config_t alarm_config = {
        .alarm_count = 5 * 1000 * 1000,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = false,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(gptimer, &alarm_config));
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(gptimer, &(gptimer_event_callbacks_t){
        .on_alarm = isr_timer_callback,
    }, NULL));
    ESP_ERROR_CHECK(gptimer_enable(gptimer));
    ESP_ERROR_CHECK(gptimer_start(gptimer));

    ESP_LOGI(TAG, "gptimer started, waiting for ISR fire + upload drain");

    vTaskDelay(pdMS_TO_TICKS(20000));

    UBaseType_t final_hwm = uxTaskGetStackHighWaterMark(NULL);
    ESP_LOGI(TAG, "ISR_HIL_RESULT fire_count=%d telemetry_ok=%d status_ok=%d overflow_rejected=%d isr_woken=%d initial_hwm=%lu final_hwm=%lu",
             isr_fire_count, isr_telemetry_ok, isr_status_ok, isr_overflow_rejected, (int)isr_woken,
             (unsigned long)initial_hwm, (unsigned long)final_hwm);

    if (isr_fire_count == 0) {
        ESP_LOGE(TAG, "ISR_HIL_FAIL: timer ISR did not fire");
    } else if (!isr_telemetry_ok) {
        ESP_LOGE(TAG, "ISR_HIL_FAIL: telemetry enqueue from ISR failed");
    } else if (!isr_status_ok) {
        ESP_LOGE(TAG, "ISR_HIL_FAIL: status enqueue from ISR failed");
    } else if (!isr_overflow_rejected) {
        ESP_LOGE(TAG, "ISR_HIL_FAIL: overflow telemetry was not rejected");
    } else if (final_hwm < 256) {
        ESP_LOGE(TAG, "ISR_HIL_FAIL: stack high water mark too low (%lu)", (unsigned long)final_hwm);
    } else {
        ESP_LOGI(TAG, "ISR_HIL_PASS");
    }

    ESP_LOGI(TAG, "ISR_HIL_DONE");
}
