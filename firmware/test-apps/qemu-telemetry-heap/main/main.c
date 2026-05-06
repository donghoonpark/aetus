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

static esp_err_t build_dynamic_telemetry(aetus_telemetry_t *telemetry)
{
    static const uint8_t inline_bytes[] = {0xde, 0xad, 0xbe, 0xef};
    static const uint8_t heap_bytes[] = {0xca, 0xfe, 0xba, 0xbe, 0x42};

    aetus_telemetry_init(telemetry);
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_int64(telemetry, "m0", 100, "u"), TAG, "m0 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_double(telemetry, "m1", 22.5, "c"), TAG, "m1 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_string(telemetry, "m2", "inline-str", "s"), TAG, "m2 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_bytes(telemetry, "m3", inline_bytes, sizeof(inline_bytes), "b"), TAG, "m3 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_int64(telemetry, "m4", 500, "u"), TAG, "m4 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_string(telemetry, "m5", "heap-str", "s"), TAG, "m5 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_bytes(telemetry, "m6", heap_bytes, sizeof(heap_bytes), "b"), TAG, "m6 failed");
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

    aetus_telemetry_deinit(&producer);

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
    vTaskDelay(pdMS_TO_TICKS(3000));
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
    printf(
        "AETUS_TELEMETRY_HEAP_STATS "
        "iterations=%lu "
        "heap_metrics_released=%lu "
        "blobs_released=%lu "
        "baseline_free=%lu "
        "final_free=%lu\n",
        (unsigned long)TEST_ITERATIONS,
        (unsigned long)stats.telemetry_heap_metrics_released,
        (unsigned long)stats.telemetry_blobs_released,
        (unsigned long)baseline,
        (unsigned long)final_free
    );

    bool release_counts_ok =
        stats.telemetry_heap_metrics_released == TEST_ITERATIONS &&
        stats.telemetry_blobs_released == (TEST_ITERATIONS * 4U);
    bool heap_ok = final_free + HEAP_TOLERANCE_BYTES >= baseline;

    if (release_counts_ok && heap_ok) {
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
