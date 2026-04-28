#include "mock_device_core.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(
            stderr,
            "usage: %s <telemetry|status> <device_id> <boot_id> <sequence> [reboot_reason] [timestamp_ns]\n",
            argv[0]
        );
        return 1;
    }

    const char *mode = argv[1];
    const char *device_id = argv[2];
    const char *boot_id = argv[3];
    uint64_t sequence = strtoull(argv[4], NULL, 10);

    uint8_t buffer[512];
    size_t encoded_size = 0;
    bool ok = false;

    if (strcmp(mode, "telemetry") == 0) {
        uint64_t timestamp_ns = argc >= 6 ? strtoull(argv[5], NULL, 10) : 0;
        ok = encode_telemetry_event(buffer, sizeof(buffer), &encoded_size, device_id, boot_id, sequence, timestamp_ns);
    } else if (strcmp(mode, "status") == 0) {
        const char *reboot_reason = argc >= 6 ? argv[5] : "power_on";
        uint64_t timestamp_ns = argc >= 7 ? strtoull(argv[6], NULL, 10) : 0;
        ok = encode_status_event(
            buffer,
            sizeof(buffer),
            &encoded_size,
            device_id,
            boot_id,
            sequence,
            reboot_reason,
            timestamp_ns
        );
    } else {
        fprintf(stderr, "unknown mode: %s\n", mode);
        return 2;
    }

    if (!ok) {
        fprintf(stderr, "encoding failed\n");
        return 3;
    }

    if (fwrite(buffer, 1, encoded_size, stdout) != encoded_size) {
        fprintf(stderr, "failed to write output\n");
        return 4;
    }

    return 0;
}
