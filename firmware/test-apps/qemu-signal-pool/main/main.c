#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "aetus_signal_sample_pool.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define CONTRACT_SAMPLE_RATE_HZ 200U
#define CONTRACT_DURATION_S 3U
#define CONTRACT_CHANNEL_COUNT 2U
#define CONTRACT_SAMPLE_WIDTH_BYTES 2U
#define CONTRACT_SAMPLE_COUNT (CONTRACT_SAMPLE_RATE_HZ * CONTRACT_DURATION_S)
#define CONTRACT_SAMPLE_BYTES (CONTRACT_SAMPLE_COUNT * CONTRACT_CHANNEL_COUNT * CONTRACT_SAMPLE_WIDTH_BYTES)

AETUS_STATIC_ASSERT(CONTRACT_SAMPLE_BYTES == 2400U, "qemu signal pool contract must stay at 2400 bytes");

typedef struct {
    void *owner;
    const uint8_t *samples;
    size_t samples_size;
} queued_signal_t;

static void print_stats(const aetus_signal_sample_pool_stats_t *stats)
{
    printf(
        "AETUS_SIGNAL_POOL_STATS "
        "allocated_blocks=%lu "
        "peak_allocated_blocks=%lu "
        "allocation_count=%lu "
        "release_count=%lu "
        "queue_send_failure_release_count=%lu "
        "allocation_failure_count=%lu\n",
        (unsigned long)stats->allocated_blocks,
        (unsigned long)stats->peak_allocated_blocks,
        (unsigned long)stats->allocation_count,
        (unsigned long)stats->release_count,
        (unsigned long)stats->queue_send_failure_release_count,
        (unsigned long)stats->allocation_failure_count
    );
}

static esp_err_t enqueue_sample(
    QueueHandle_t queue,
    aetus_signal_sample_pool_stats_t *stats,
    const uint8_t *samples,
    size_t samples_size
)
{
    void *owner = NULL;
    uint8_t *owned_samples = aetus_signal_sample_pool_alloc(stats, samples_size, &owner);
    ESP_RETURN_ON_FALSE(owned_samples != NULL, ESP_ERR_NO_MEM, "qemu_pool", "pool allocation failed");
    memcpy(owned_samples, samples, samples_size);

    queued_signal_t item = {
        .owner = owner,
        .samples = owned_samples,
        .samples_size = samples_size,
    };
    if (xQueueSend(queue, &item, 0) != pdTRUE) {
        aetus_signal_sample_pool_note_queue_send_failure(stats);
        printf("signal frame enqueue failed because queue is full\n");
        aetus_signal_sample_pool_release(stats, owner);
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

void app_main(void)
{
    static const uint8_t samples[CONTRACT_SAMPLE_BYTES] = {0};
    aetus_signal_sample_pool_stats_t stats;

    vTaskDelay(pdMS_TO_TICKS(5000));
    printf("AETUS_SIGNAL_POOL_APP_MAIN\n");
    fflush(stdout);

    aetus_signal_sample_pool_reset(&stats);
    QueueHandle_t queue = xQueueCreate(1, sizeof(queued_signal_t));
    ESP_ERROR_CHECK(queue == NULL ? ESP_ERR_NO_MEM : ESP_OK);

    ESP_ERROR_CHECK(enqueue_sample(queue, &stats, samples, sizeof(samples)));
    ESP_ERROR_CHECK(enqueue_sample(queue, &stats, samples, sizeof(samples)) == ESP_ERR_TIMEOUT ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(enqueue_sample(queue, &stats, samples, sizeof(samples)) == ESP_ERR_TIMEOUT ? ESP_OK : ESP_FAIL);

    aetus_signal_sample_pool_stats_t snapshot;
    aetus_signal_sample_pool_copy_stats(&snapshot, &stats);
    print_stats(&snapshot);

    if (snapshot.allocated_blocks == 1U &&
        snapshot.peak_allocated_blocks == 2U &&
        snapshot.allocation_count == 3U &&
        snapshot.release_count == 2U &&
        snapshot.queue_send_failure_release_count == 2U &&
        snapshot.allocation_failure_count == 0U) {
        printf("AETUS_SIGNAL_POOL_HEAP_PASS\n");
    } else {
        printf("AETUS_SIGNAL_POOL_HEAP_FAIL\n");
    }
    printf("AETUS_SIGNAL_POOL_TEST_DONE\n");
    fflush(stdout);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
