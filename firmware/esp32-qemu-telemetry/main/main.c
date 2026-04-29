#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pb_encode.h"

#include "ingest.pb.h"

#define AETUS_QEMU_DEVICE_ID "esp32c5-test-001"
#define AETUS_QEMU_BOOT_ID "boot-qemu-0001"
#define AETUS_QEMU_SEQUENCE 7ULL
#define AETUS_QEMU_TIMESTAMP_NS 1712345678901235000ULL

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }

    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static bool encode_telemetry_event(uint8_t *buffer, size_t buffer_size, size_t *encoded_size)
{
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    event.schema_version = 1;
    copy_string(event.device_id, sizeof(event.device_id), AETUS_QEMU_DEVICE_ID);
    event.sequence = AETUS_QEMU_SEQUENCE;
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    copy_string(event.boot_id, sizeof(event.boot_id), AETUS_QEMU_BOOT_ID);
    event.firmware_version = 1002003;
    event.uptime_ms = 1234;
    event.timestamp_ns = AETUS_QEMU_TIMESTAMP_NS;

    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;
    event.body.telemetry.metrics_count = 1;

    aetus_ingest_v1_Metric *metric = &event.body.telemetry.metrics[0];
    copy_string(metric->key, sizeof(metric->key), "temperature");
    metric->which_value = aetus_ingest_v1_Metric_double_value_tag;
    metric->value.double_value = 22.25;
    copy_string(metric->unit, sizeof(metric->unit), "celsius");

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

void app_main(void)
{
    uint8_t buffer[256];
    size_t encoded_size = 0;

    vTaskDelay(pdMS_TO_TICKS(5000));

    if (!encode_telemetry_event(buffer, sizeof(buffer), &encoded_size)) {
        printf("AETUS_PROTO_ERROR\n");
        fflush(stdout);
        return;
    }

    printf("AETUS_PROTO_HEX_BEGIN\n");
    for (size_t index = 0; index < encoded_size; index++) {
        printf("%02x", buffer[index]);
    }
    printf("\nAETUS_PROTO_HEX_END\n");
    fflush(stdout);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
