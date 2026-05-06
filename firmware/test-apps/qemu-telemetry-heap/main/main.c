#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "aetus.h"
#include "aetus_internal.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TEST_ITERATIONS 20U
#define HEAP_TOLERANCE_BYTES 512U

static const char *TAG = "qemu_metric_heap";

static void telemetry_init(aetus_telemetry_t *telemetry)
{
    memset(telemetry, 0, sizeof(*telemetry));
    telemetry->capacity = AETUS_TELEMETRY_INLINE_METRICS;
}

static void telemetry_deinit(aetus_telemetry_t *telemetry)
{
    if (telemetry == NULL) {
        return;
    }

    uint32_t count = telemetry->metric_count;
    for (uint32_t i = 0; i < count && i < AETUS_TELEMETRY_INLINE_METRICS; i++) {
        aetus_metric_t *metric = &telemetry->inline_metrics[i];
        if ((metric->type == AETUS_METRIC_VALUE_STRING || metric->type == AETUS_METRIC_VALUE_BYTES)
            && metric->value.blob_data != NULL) {
            vPortFree(metric->value.blob_data);
        }
    }

    if (telemetry->heap_metrics != NULL) {
        uint32_t heap_count = count > AETUS_TELEMETRY_INLINE_METRICS
            ? count - AETUS_TELEMETRY_INLINE_METRICS : 0;
        for (uint32_t i = 0; i < heap_count; i++) {
            aetus_metric_t *metric = &telemetry->heap_metrics[i];
            if ((metric->type == AETUS_METRIC_VALUE_STRING || metric->type == AETUS_METRIC_VALUE_BYTES)
                && metric->value.blob_data != NULL) {
                vPortFree(metric->value.blob_data);
            }
        }
        vPortFree(telemetry->heap_metrics);
    }
    telemetry_init(telemetry);
}

static void copy_limited_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0U) {
        return;
    }
    size_t index = 0;
    while (index + 1U < target_size && source[index] != '\0') {
        target[index] = source[index];
        index++;
    }
    target[index] = '\0';
}

static esp_err_t telemetry_next_metric(aetus_telemetry_t *telemetry, aetus_metric_t **metric)
{
    ESP_RETURN_ON_FALSE(telemetry->metric_count < AETUS_MAX_METRICS, ESP_ERR_INVALID_SIZE, TAG, "too many metrics");
    if (telemetry->metric_count >= AETUS_TELEMETRY_INLINE_METRICS && telemetry->heap_metrics == NULL) {
        uint32_t heap_slots = AETUS_MAX_METRICS - AETUS_TELEMETRY_INLINE_METRICS;
        telemetry->heap_metrics = (aetus_metric_t *)pvPortMalloc(heap_slots * sizeof(aetus_metric_t));
        ESP_RETURN_ON_FALSE(telemetry->heap_metrics != NULL, ESP_ERR_NO_MEM, TAG, "heap metrics allocation failed");
        memset(telemetry->heap_metrics, 0, heap_slots * sizeof(aetus_metric_t));
        telemetry->capacity = AETUS_MAX_METRICS;
    }

    if (telemetry->metric_count < AETUS_TELEMETRY_INLINE_METRICS) {
        *metric = &telemetry->inline_metrics[telemetry->metric_count];
    } else {
        *metric = &telemetry->heap_metrics[telemetry->metric_count - AETUS_TELEMETRY_INLINE_METRICS];
    }
    telemetry->metric_count++;
    return ESP_OK;
}

static esp_err_t telemetry_add_int64(aetus_telemetry_t *telemetry, const char *key, int64_t value, const char *unit)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(telemetry_next_metric(telemetry, &metric), TAG, "metric slot failed");
    copy_limited_string(metric->key, sizeof(metric->key), key);
    copy_limited_string(metric->unit, sizeof(metric->unit), unit);
    metric->type = AETUS_METRIC_VALUE_INT64;
    metric->value.int64_value = value;
    return ESP_OK;
}

static esp_err_t telemetry_add_double(aetus_telemetry_t *telemetry, const char *key, double value, const char *unit)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(telemetry_next_metric(telemetry, &metric), TAG, "metric slot failed");
    copy_limited_string(metric->key, sizeof(metric->key), key);
    copy_limited_string(metric->unit, sizeof(metric->unit), unit);
    metric->type = AETUS_METRIC_VALUE_DOUBLE;
    metric->value.double_value = value;
    return ESP_OK;
}

static esp_err_t telemetry_add_blob(
    aetus_telemetry_t *telemetry,
    const char *key,
    const void *value,
    uint32_t value_size,
    const char *unit,
    aetus_metric_value_type_t type
)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(telemetry_next_metric(telemetry, &metric), TAG, "metric slot failed");
    copy_limited_string(metric->key, sizeof(metric->key), key);
    copy_limited_string(metric->unit, sizeof(metric->unit), unit);
    metric->type = type;
    metric->blob_size = value_size;

    size_t alloc_size = value_size;
    if (type == AETUS_METRIC_VALUE_STRING) {
        alloc_size++;
    }
    metric->value.blob_data = pvPortMalloc(alloc_size);
    ESP_RETURN_ON_FALSE(metric->value.blob_data != NULL, ESP_ERR_NO_MEM, TAG, "blob allocation failed");
    memcpy(metric->value.blob_data, value, alloc_size);
    return ESP_OK;
}

static esp_err_t build_dynamic_telemetry(aetus_telemetry_t *telemetry)
{
    static const uint8_t inline_bytes[] = {0xde, 0xad, 0xbe, 0xef};
    static const uint8_t heap_bytes[] = {0xca, 0xfe, 0xba, 0xbe, 0x42};

    telemetry_init(telemetry);
    ESP_RETURN_ON_ERROR(telemetry_add_int64(telemetry, "m0", 100, "u"), TAG, "m0 failed");
    ESP_RETURN_ON_ERROR(telemetry_add_double(telemetry, "m1", 22.5, "c"), TAG, "m1 failed");
    ESP_RETURN_ON_ERROR(
        telemetry_add_blob(telemetry, "m2", "inline-str", strlen("inline-str"), "s", AETUS_METRIC_VALUE_STRING),
        TAG,
        "m2 failed"
    );
    ESP_RETURN_ON_ERROR(
        telemetry_add_blob(telemetry, "m3", inline_bytes, sizeof(inline_bytes), "b", AETUS_METRIC_VALUE_BYTES),
        TAG,
        "m3 failed"
    );
    ESP_RETURN_ON_ERROR(telemetry_add_int64(telemetry, "m4", 500, "u"), TAG, "m4 failed");
    ESP_RETURN_ON_ERROR(
        telemetry_add_blob(telemetry, "m5", "heap-str", strlen("heap-str"), "s", AETUS_METRIC_VALUE_STRING),
        TAG,
        "m5 failed"
    );
    ESP_RETURN_ON_ERROR(
        telemetry_add_blob(telemetry, "m6", heap_bytes, sizeof(heap_bytes), "b", AETUS_METRIC_VALUE_BYTES),
        TAG,
        "m6 failed"
    );
    return ESP_OK;
}

static bool queue_item_has_expected_copy(const aetus_telemetry_t *producer, const aetus_queue_item_t *item)
{
    if (producer->metric_count != 7U || item->body.telemetry.metric_count != 7U) {
        return false;
    }
    if (producer->heap_metrics == NULL || item->body.telemetry.heap_metrics == NULL) {
        return false;
    }
    if (producer->heap_metrics == item->body.telemetry.heap_metrics) {
        return false;
    }

    const aetus_metric_t *producer_inline_string = &producer->inline_metrics[2];
    const aetus_metric_t *item_inline_string = &item->body.telemetry.inline_metrics[2];
    const aetus_metric_t *producer_inline_bytes = &producer->inline_metrics[3];
    const aetus_metric_t *item_inline_bytes = &item->body.telemetry.inline_metrics[3];
    const aetus_metric_t *producer_heap_string = &producer->heap_metrics[1];
    const aetus_metric_t *item_heap_string = &item->body.telemetry.heap_metrics[1];
    const aetus_metric_t *producer_heap_bytes = &producer->heap_metrics[2];
    const aetus_metric_t *item_heap_bytes = &item->body.telemetry.heap_metrics[2];

    if (producer_inline_string->value.blob_data == item_inline_string->value.blob_data) {
        return false;
    }
    if (producer_inline_bytes->value.blob_data == item_inline_bytes->value.blob_data) {
        return false;
    }
    if (producer_heap_string->value.blob_data == item_heap_string->value.blob_data) {
        return false;
    }
    if (producer_heap_bytes->value.blob_data == item_heap_bytes->value.blob_data) {
        return false;
    }

    return strcmp((const char *)item_inline_string->value.blob_data, "inline-str") == 0 &&
        strcmp((const char *)item_heap_string->value.blob_data, "heap-str") == 0 &&
        item->body.telemetry.heap_metrics[0].value.int64_value == 500 &&
        item_inline_bytes->blob_size == 4U &&
        item_heap_bytes->blob_size == 5U;
}

static esp_err_t run_once(void)
{
    aetus_telemetry_t producer;
    aetus_queue_item_t item;

    memset(&item, 0, sizeof(item));
    ESP_RETURN_ON_ERROR(build_dynamic_telemetry(&producer), TAG, "build telemetry failed");
    ESP_RETURN_ON_ERROR(aetus_test_copy_telemetry_to_queue_item(&producer, &item), TAG, "copy failed");
    ESP_RETURN_ON_FALSE(queue_item_has_expected_copy(&producer, &item), ESP_FAIL, TAG, "copy contract failed");

    telemetry_deinit(&producer);

    ESP_RETURN_ON_FALSE(
        item.body.telemetry.heap_metrics != NULL,
        ESP_FAIL,
        TAG,
        "queue item lost heap metrics after producer deinit"
    );
    ESP_RETURN_ON_FALSE(
        strcmp((const char *)item.body.telemetry.heap_metrics[1].value.blob_data, "heap-str") == 0,
        ESP_FAIL,
        TAG,
        "queue item heap blob corrupted after producer deinit"
    );

    aetus_test_release_queue_item(&item);
    return ESP_OK;
}

void app_main(void)
{
    printf("AETUS_TELEMETRY_HEAP_APP_MAIN\n");
    fflush(stdout);

    aetus_test_release_stats_t stats;

    aetus_test_reset_release_stats();
    ESP_ERROR_CHECK(run_once());

    size_t baseline = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    aetus_test_reset_release_stats();
    for (uint32_t i = 0; i < TEST_ITERATIONS; i++) {
        ESP_ERROR_CHECK(run_once());
    }
    size_t final_free = heap_caps_get_free_size(MALLOC_CAP_8BIT);

    aetus_test_get_release_stats(&stats);
    bool release_counts_ok =
        stats.telemetry_heap_metrics_released == TEST_ITERATIONS &&
        stats.telemetry_blobs_released == (TEST_ITERATIONS * 4U);
    bool heap_ok = final_free + HEAP_TOLERANCE_BYTES >= baseline;
    bool passed = release_counts_ok && heap_ok;

    printf(
        "AETUS_TELEMETRY_HEAP_STATS "
        "status=%s "
        "iterations=%lu "
        "heap_metrics_released=%lu "
        "blobs_released=%lu "
        "baseline_free=%lu "
        "final_free=%lu\n",
        passed ? "pass" : "fail",
        (unsigned long)TEST_ITERATIONS,
        (unsigned long)stats.telemetry_heap_metrics_released,
        (unsigned long)stats.telemetry_blobs_released,
        (unsigned long)baseline,
        (unsigned long)final_free
    );

    if (passed) {
        printf("AETUS_TELEMETRY_HEAP_PASS\n");
    } else {
        printf("AETUS_TELEMETRY_HEAP_FAIL release_counts_ok=%d heap_ok=%d\n",
               release_counts_ok, heap_ok);
    }
    printf("AETUS_TELEMETRY_HEAP_TEST_DONE\n");
    fflush(stdout);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
