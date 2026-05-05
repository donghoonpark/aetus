#pragma once

#include <stddef.h>
#include <stdint.h>

#include "aetus.h"

#ifdef __cplusplus
extern "C" {
#endif

void aetus_signal_sample_pool_reset(aetus_signal_sample_pool_stats_t *stats);
uint8_t *aetus_signal_sample_pool_alloc(
    aetus_signal_sample_pool_stats_t *stats,
    size_t size,
    void **owner
);
void aetus_signal_sample_pool_release(
    aetus_signal_sample_pool_stats_t *stats,
    void *owner
);
void aetus_signal_sample_pool_note_queue_send_failure(aetus_signal_sample_pool_stats_t *stats);
void aetus_signal_sample_pool_note_validation_failure(aetus_signal_sample_pool_stats_t *stats);
void aetus_signal_sample_pool_note_upload_success(aetus_signal_sample_pool_stats_t *stats);
void aetus_signal_sample_pool_note_final_drop(aetus_signal_sample_pool_stats_t *stats);
void aetus_signal_sample_pool_copy_stats(
    aetus_signal_sample_pool_stats_t *target,
    const aetus_signal_sample_pool_stats_t *source
);

#ifdef __cplusplus
}
#endif
