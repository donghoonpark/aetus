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
