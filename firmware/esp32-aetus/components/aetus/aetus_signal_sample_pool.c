#include "aetus_signal_sample_pool.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

static const char *TAG = "aetus";

typedef struct {
    size_t size;
    uint8_t data[];
} aetus_heap_signal_sample_block_t;

static portMUX_TYPE s_signal_pool_lock = portMUX_INITIALIZER_UNLOCKED;

static void signal_sample_pool_note_alloc(aetus_signal_sample_pool_stats_t *stats, size_t size)
{
    stats->allocated_blocks++;
    stats->allocation_count++;
    stats->allocated_bytes += size;
    if (stats->allocated_blocks > stats->peak_allocated_blocks) {
        stats->peak_allocated_blocks = stats->allocated_blocks;
    }
    if (stats->allocated_bytes > stats->peak_allocated_bytes) {
        stats->peak_allocated_bytes = stats->allocated_bytes;
    }
}

static void signal_sample_pool_note_release(aetus_signal_sample_pool_stats_t *stats, size_t size)
{
    if (stats->allocated_blocks > 0U) {
        stats->allocated_blocks--;
    }
    if (stats->allocated_bytes >= size) {
        stats->allocated_bytes -= size;
    } else {
        stats->allocated_bytes = 0U;
    }
    stats->release_count++;
}

void aetus_signal_sample_pool_reset(aetus_signal_sample_pool_stats_t *stats)
{
    if (stats != NULL) {
        portENTER_CRITICAL(&s_signal_pool_lock);
        memset(stats, 0, sizeof(*stats));
        portEXIT_CRITICAL(&s_signal_pool_lock);
    }
}

uint8_t *aetus_signal_sample_pool_alloc(
    aetus_signal_sample_pool_stats_t *stats,
    size_t size,
    void **owner
)
{
    if (owner == NULL || stats == NULL) {
        return NULL;
    }
    *owner = NULL;
    if (size == 0U || size > AETUS_SIGNAL_SAMPLES_MAX) {
        portENTER_CRITICAL(&s_signal_pool_lock);
        stats->allocation_failure_count++;
        portEXIT_CRITICAL(&s_signal_pool_lock);
        ESP_LOGW(TAG, "signal sample pool allocation rejected size=%u max=%u", (unsigned)size, (unsigned)AETUS_SIGNAL_SAMPLES_MAX);
        return NULL;
    }

    aetus_heap_signal_sample_block_t *block = (aetus_heap_signal_sample_block_t *)pvPortMalloc(sizeof(*block) + size);
    if (block == NULL) {
        portENTER_CRITICAL(&s_signal_pool_lock);
        stats->allocation_failure_count++;
        portEXIT_CRITICAL(&s_signal_pool_lock);
        ESP_LOGW(TAG, "signal sample heap pool exhausted size=%u", (unsigned)size);
        return NULL;
    }
    block->size = size;
    *owner = block;
    portENTER_CRITICAL(&s_signal_pool_lock);
    signal_sample_pool_note_alloc(stats, size);
    portEXIT_CRITICAL(&s_signal_pool_lock);
    return block->data;
}

void aetus_signal_sample_pool_release(
    aetus_signal_sample_pool_stats_t *stats,
    void *owner
)
{
    if (owner == NULL || stats == NULL) {
        return;
    }

    aetus_heap_signal_sample_block_t *block = (aetus_heap_signal_sample_block_t *)owner;
    size_t size = block->size;
    vPortFree(block);
    portENTER_CRITICAL(&s_signal_pool_lock);
    signal_sample_pool_note_release(stats, size);
    portEXIT_CRITICAL(&s_signal_pool_lock);
}

void aetus_signal_sample_pool_note_queue_send_failure(aetus_signal_sample_pool_stats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_signal_pool_lock);
    stats->queue_send_failure_release_count++;
    portEXIT_CRITICAL(&s_signal_pool_lock);
}

void aetus_signal_sample_pool_note_validation_failure(aetus_signal_sample_pool_stats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_signal_pool_lock);
    stats->validation_failure_release_count++;
    portEXIT_CRITICAL(&s_signal_pool_lock);
}

void aetus_signal_sample_pool_note_upload_success(aetus_signal_sample_pool_stats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_signal_pool_lock);
    stats->upload_success_release_count++;
    portEXIT_CRITICAL(&s_signal_pool_lock);
}

void aetus_signal_sample_pool_note_final_drop(aetus_signal_sample_pool_stats_t *stats)
{
    if (stats == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_signal_pool_lock);
    stats->final_drop_release_count++;
    portEXIT_CRITICAL(&s_signal_pool_lock);
}

void aetus_signal_sample_pool_copy_stats(
    aetus_signal_sample_pool_stats_t *target,
    const aetus_signal_sample_pool_stats_t *source
)
{
    if (target == NULL || source == NULL) {
        return;
    }
    portENTER_CRITICAL(&s_signal_pool_lock);
    *target = *source;
    portEXIT_CRITICAL(&s_signal_pool_lock);
}
