#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/gptimer.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "aetus.h"
#include "aetus_internal.h"

#define TEST_QUEUE_DEPTH 8
#define TEST_TIMER_TIMEOUT_US 100000

static QueueHandle_t test_queue = NULL;
static UBaseType_t main_task_initial_hwm = 0;

static void test_copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }
    size_t index = 0;
    while (index + 1 < target_size && source[index] != '\0') {
        target[index] = source[index];
        index++;
    }
    target[index] = '\0';
}

static void test_telemetry_init(aetus_telemetry_t *telemetry)
{
    memset(telemetry, 0, sizeof(*telemetry));
    telemetry->capacity = AETUS_TELEMETRY_INLINE_METRICS;
}

static void test_telemetry_add_double(
    aetus_telemetry_t *telemetry,
    const char *key,
    double value,
    const char *unit
)
{
    if (telemetry->metric_count >= AETUS_TELEMETRY_INLINE_METRICS) {
        return;
    }
    aetus_metric_t *metric = &telemetry->inline_metrics[telemetry->metric_count++];
    memset(metric, 0, sizeof(*metric));
    test_copy_string(metric->key, sizeof(metric->key), key);
    test_copy_string(metric->unit, sizeof(metric->unit), unit);
    metric->type = AETUS_METRIC_VALUE_DOUBLE;
    metric->value.double_value = value;
}

static void test_telemetry_add_int64(
    aetus_telemetry_t *telemetry,
    const char *key,
    int64_t value,
    const char *unit
)
{
    if (telemetry->metric_count >= AETUS_TELEMETRY_INLINE_METRICS) {
        return;
    }
    aetus_metric_t *metric = &telemetry->inline_metrics[telemetry->metric_count++];
    memset(metric, 0, sizeof(*metric));
    test_copy_string(metric->key, sizeof(metric->key), key);
    test_copy_string(metric->unit, sizeof(metric->unit), unit);
    metric->type = AETUS_METRIC_VALUE_INT64;
    metric->value.int64_value = value;
}

static void test_status_init(aetus_status_t *status, aetus_device_status_t device_status)
{
    memset(status, 0, sizeof(*status));
    status->status = device_status;
}

static bool IRAM_ATTR timer_isr_callback(gptimer_handle_t timer, const gptimer_alarm_event_data_t *edata, void *user_ctx)
{
    (void)timer;
    (void)edata;
    (void)user_ctx;

    BaseType_t pxHigherPriorityTaskWoken = pdFALSE;

    aetus_telemetry_t telemetry;
    test_telemetry_init(&telemetry);
    test_telemetry_add_double(&telemetry, "temperature", 22.25, "celsius");
    test_telemetry_add_int64(&telemetry, "battery_mv", 3800, "mV");

    aetus_queue_item_t item = {
        .kind = AETUS_QUEUE_ITEM_TELEMETRY,
        .body.telemetry = telemetry,
    };
    xQueueSendFromISR(test_queue, &item, &pxHigherPriorityTaskWoken);

    aetus_status_t status;
    test_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
    test_copy_string(status.reboot_reason, sizeof(status.reboot_reason), "isr_test_start");

    aetus_queue_item_t status_item = {
        .kind = AETUS_QUEUE_ITEM_STATUS,
        .body.status = status,
    };
    xQueueSendFromISR(test_queue, &status_item, &pxHigherPriorityTaskWoken);

    return pxHigherPriorityTaskWoken != pdFALSE;
}

void app_main(void)
{
    main_task_initial_hwm = uxTaskGetStackHighWaterMark(NULL);
    printf("AETUS_ISR_ENQUEUE_BEGIN initial_hwm=%lu\n", (unsigned long)main_task_initial_hwm);
    fflush(stdout);

    test_queue = xQueueCreate(TEST_QUEUE_DEPTH, sizeof(aetus_queue_item_t));
    if (test_queue == NULL) {
        printf("AETUS_ISR_ENQUEUE_FAIL queue_create_failed\n");
        fflush(stdout);
        vTaskSuspend(NULL);
        return;
    }

    gptimer_handle_t gptimer = NULL;
    gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1 * 1000 * 1000,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&timer_config, &gptimer));

    gptimer_alarm_config_t alarm_config = {
        .alarm_count = TEST_TIMER_TIMEOUT_US,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = false,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(gptimer, &alarm_config));
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(gptimer, &(gptimer_event_callbacks_t){
        .on_alarm = timer_isr_callback,
    }, NULL));
    ESP_ERROR_CHECK(gptimer_enable(gptimer));
    ESP_ERROR_CHECK(gptimer_start(gptimer));

    aetus_queue_item_t received[2];
    int received_count = 0;
    TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(5000);

    while (received_count < 2 && xTaskGetTickCount() < deadline) {
        aetus_queue_item_t buf;
        if (xQueueReceive(test_queue, &buf, pdMS_TO_TICKS(1000)) == pdTRUE) {
            received[received_count] = buf;
            received_count++;
        }
    }

    ESP_ERROR_CHECK(gptimer_stop(gptimer));
    ESP_ERROR_CHECK(gptimer_disable(gptimer));
    ESP_ERROR_CHECK(gptimer_del_timer(gptimer));
    vQueueDelete(test_queue);

    UBaseType_t final_hwm = uxTaskGetStackHighWaterMark(NULL);
    printf(
        "AETUS_ISR_ENQUEUE_RESULT received=%d initial_hwm=%lu final_hwm=%lu\n",
        received_count, (unsigned long)main_task_initial_hwm, (unsigned long)final_hwm
    );
    fflush(stdout);

    bool all_ok = true;

    if (received_count != 2) {
        printf("AETUS_ISR_ENQUEUE_FAIL expected 2 items, got %d\n", received_count);
        all_ok = false;
    }

    bool found_telemetry = false;
    bool found_status = false;
    for (int i = 0; i < received_count; i++) {
        if (received[i].kind == AETUS_QUEUE_ITEM_TELEMETRY) {
            found_telemetry = true;
            if (received[i].body.telemetry.metric_count != 2) {
                printf("AETUS_ISR_ENQUEUE_FAIL telemetry metric_count=%lu\n",
                       (unsigned long)received[i].body.telemetry.metric_count);
                all_ok = false;
            }
        }
        if (received[i].kind == AETUS_QUEUE_ITEM_STATUS) {
            found_status = true;
        }
    }

    if (!found_telemetry) {
        printf("AETUS_ISR_ENQUEUE_FAIL telemetry item not found\n");
        all_ok = false;
    }
    if (!found_status) {
        printf("AETUS_ISR_ENQUEUE_FAIL status item not found\n");
        all_ok = false;
    }

    if (final_hwm < 256) {
        printf("AETUS_ISR_ENQUEUE_FAIL stack dangerously low: final_hwm=%lu\n", (unsigned long)final_hwm);
        all_ok = false;
    }

    if (all_ok) {
        printf("AETUS_ISR_ENQUEUE_PASS\n");
    }

    printf("AETUS_ISR_ENQUEUE_DONE\n");
    fflush(stdout);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
