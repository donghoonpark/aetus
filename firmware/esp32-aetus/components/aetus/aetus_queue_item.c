#include <stdbool.h>
#include <string.h>

#include "aetus_internal.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "aetus_queue_item";

#ifdef CONFIG_AETUS_TEST_HOOKS
static aetus_test_release_stats_t s_test_release_stats;
#endif

void aetus_internal_release_telemetry_queue_item(aetus_queue_item_t *item)
{
    if (item == NULL || item->kind != AETUS_QUEUE_ITEM_TELEMETRY) {
        return;
    }

    aetus_telemetry_t *t = &item->body.telemetry;
    uint32_t count = t->metric_count;
    for (uint32_t i = 0; i < count && i < AETUS_TELEMETRY_INLINE_METRICS; i++) {
        aetus_metric_t *m = &t->inline_metrics[i];
        if ((m->type == AETUS_METRIC_VALUE_STRING || m->type == AETUS_METRIC_VALUE_BYTES)
            && m->value.blob_data != NULL) {
            vPortFree(m->value.blob_data);
#ifdef CONFIG_AETUS_TEST_HOOKS
            s_test_release_stats.telemetry_blobs_released++;
#endif
            m->value.blob_data = NULL;
            m->blob_size = 0;
        }
    }
    if (t->heap_metrics != NULL) {
        uint32_t heap_count = count > AETUS_TELEMETRY_INLINE_METRICS
            ? count - AETUS_TELEMETRY_INLINE_METRICS : 0;
        for (uint32_t i = 0; i < heap_count; i++) {
            aetus_metric_t *m = &t->heap_metrics[i];
            if ((m->type == AETUS_METRIC_VALUE_STRING || m->type == AETUS_METRIC_VALUE_BYTES)
                && m->value.blob_data != NULL) {
                vPortFree(m->value.blob_data);
#ifdef CONFIG_AETUS_TEST_HOOKS
                s_test_release_stats.telemetry_blobs_released++;
#endif
                m->value.blob_data = NULL;
                m->blob_size = 0;
            }
        }
        vPortFree(t->heap_metrics);
#ifdef CONFIG_AETUS_TEST_HOOKS
        s_test_release_stats.telemetry_heap_metrics_released++;
#endif
        t->heap_metrics = NULL;
    }
}

esp_err_t aetus_internal_copy_telemetry_to_queue_item(
    const aetus_telemetry_t *telemetry,
    aetus_queue_item_t *item
)
{
    ESP_RETURN_ON_FALSE(telemetry != NULL, ESP_ERR_INVALID_ARG, TAG, "telemetry is required");
    ESP_RETURN_ON_FALSE(item != NULL, ESP_ERR_INVALID_ARG, TAG, "queue item is required");

    memset(item, 0, sizeof(*item));
    item->kind = AETUS_QUEUE_ITEM_TELEMETRY;
    memcpy(&item->body.telemetry, telemetry, sizeof(aetus_telemetry_t));

    uint32_t count = telemetry->metric_count;
    if (telemetry->heap_metrics != NULL) {
        uint32_t heap_slots = AETUS_MAX_METRICS - AETUS_TELEMETRY_INLINE_METRICS;
        item->body.telemetry.heap_metrics = (aetus_metric_t *)pvPortMalloc(
            heap_slots * sizeof(aetus_metric_t));
        if (item->body.telemetry.heap_metrics == NULL) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(item->body.telemetry.heap_metrics, telemetry->heap_metrics,
               heap_slots * sizeof(aetus_metric_t));
    } else {
        item->body.telemetry.heap_metrics = NULL;
    }

    bool alloc_failed = false;
    for (uint32_t i = 0; i < count && !alloc_failed; i++) {
        const aetus_metric_t *src;
        aetus_metric_t *dst;
        if (i < AETUS_TELEMETRY_INLINE_METRICS) {
            src = &telemetry->inline_metrics[i];
            dst = &item->body.telemetry.inline_metrics[i];
        } else {
            uint32_t hi = i - AETUS_TELEMETRY_INLINE_METRICS;
            src = &telemetry->heap_metrics[hi];
            dst = &item->body.telemetry.heap_metrics[hi];
        }

        if ((src->type == AETUS_METRIC_VALUE_STRING
             || (src->type == AETUS_METRIC_VALUE_BYTES && src->blob_size > 0))
            && src->value.blob_data != NULL) {
            size_t copy_size = src->blob_size;
            if (src->type == AETUS_METRIC_VALUE_STRING) {
                copy_size++;
            }
            void *blob_copy = pvPortMalloc(copy_size);
            if (blob_copy == NULL) {
                alloc_failed = true;
            } else {
                memcpy(blob_copy, src->value.blob_data, copy_size);
                dst->value.blob_data = blob_copy;
                dst->blob_size = src->blob_size;
            }
        }
    }

    if (alloc_failed) {
        aetus_internal_release_telemetry_queue_item(item);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

#ifdef CONFIG_AETUS_TEST_HOOKS
void aetus_test_reset_release_stats(void)
{
    memset(&s_test_release_stats, 0, sizeof(s_test_release_stats));
}

void aetus_test_get_release_stats(aetus_test_release_stats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    *stats = s_test_release_stats;
}

esp_err_t aetus_test_copy_telemetry_to_queue_item(
    const aetus_telemetry_t *telemetry,
    aetus_queue_item_t *item
)
{
    return aetus_internal_copy_telemetry_to_queue_item(telemetry, item);
}

void aetus_test_release_queue_item(aetus_queue_item_t *item)
{
    aetus_internal_release_telemetry_queue_item(item);
}
#endif
