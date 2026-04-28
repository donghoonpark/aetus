#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool encode_telemetry_event(
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence
);

bool encode_status_event(
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence,
    const char *reboot_reason
);
