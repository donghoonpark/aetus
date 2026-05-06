#pragma once

#include "aetus.h"

typedef enum {
    AETUS_QUEUE_ITEM_TELEMETRY = 0,
    AETUS_QUEUE_ITEM_STATUS = 1,
    AETUS_QUEUE_ITEM_SIGNAL_FRAME = 2,
} aetus_queue_item_kind_t;

typedef struct {
    aetus_queue_item_kind_t kind;
    void *signal_sample_owner;
    union {
        aetus_telemetry_t telemetry;
        aetus_status_t status;
        aetus_signal_frame_t signal_frame;
    } body;
} aetus_queue_item_t;

#ifdef CONFIG_AETUS_TEST_HOOKS
typedef struct {
    uint32_t telemetry_heap_metrics_released;
    uint32_t telemetry_blobs_released;
} aetus_test_release_stats_t;

void aetus_test_reset_release_stats(void);
void aetus_test_get_release_stats(aetus_test_release_stats_t *stats);
esp_err_t aetus_test_copy_telemetry_to_queue_item(
    const aetus_telemetry_t *telemetry,
    aetus_queue_item_t *item
);
void aetus_test_release_queue_item(aetus_queue_item_t *item);
#endif
