#include "mock_device_core.h"

#include <pb_encode.h>

#include <stdio.h>
#include <string.h>

#include "ingest.pb.h"

static void fill_common(
    aetus_ingest_v1_IngestEvent *event,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence,
    uint64_t timestamp_ns
) {
    event->schema_version = 1;
    strncpy(event->device_id, device_id, sizeof(event->device_id) - 1);
    strncpy(event->boot_id, boot_id, sizeof(event->boot_id) - 1);
    event->sequence = sequence;
    event->firmware_version = 1002003;
    event->uptime_ms = 1234;
    event->timestamp_ns = timestamp_ns;
}

bool encode_telemetry_event(
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence,
    uint64_t timestamp_ns
) {
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    fill_common(&event, device_id, boot_id, sequence, timestamp_ns);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;
    event.body.telemetry.metrics_count = 1;

    aetus_ingest_v1_Metric *metric = &event.body.telemetry.metrics[0];
    strncpy(metric->key, "temperature", sizeof(metric->key) - 1);
    metric->which_value = aetus_ingest_v1_Metric_double_value_tag;
    metric->value.double_value = 22.25;
    strncpy(metric->unit, "celsius", sizeof(metric->unit) - 1);

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

bool encode_status_event(
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence,
    const char *reboot_reason,
    uint64_t timestamp_ns
) {
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    fill_common(&event, device_id, boot_id, sequence, timestamp_ns);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_STATUS;
    event.which_body = aetus_ingest_v1_IngestEvent_status_tag;
    event.body.status.status = aetus_ingest_v1_DeviceStatus_DEVICE_STATUS_ONLINE;
    event.body.status.rssi = -47;
    event.body.status.free_heap = 4096;
    strncpy(event.body.status.reboot_reason, reboot_reason, sizeof(event.body.status.reboot_reason) - 1);

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}
