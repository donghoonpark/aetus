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

bool encode_signal_frame_event(
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size,
    const char *device_id,
    const char *boot_id,
    uint64_t sequence,
    uint64_t timestamp_ns
) {
    static const float samples[] = {
        0.10f, 0.20f, 0.30f,
        0.11f, 0.21f, 0.31f,
        0.12f, 0.22f, 0.32f,
        0.13f, 0.23f, 0.33f,
    };

    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    fill_common(&event, device_id, boot_id, sequence, timestamp_ns);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;

    aetus_ingest_v1_TelemetryPayload *telemetry = &event.body.telemetry;
    telemetry->has_signal_frame = true;

    aetus_ingest_v1_SignalFrame *frame = &telemetry->signal_frame;
    strncpy(frame->stream_key, "imu.accel", sizeof(frame->stream_key) - 1);
    frame->sample_interval_ns = 5000000;
    frame->sample_count = 4;
    frame->encoding = aetus_ingest_v1_SignalSampleEncoding_SIGNAL_SAMPLE_ENCODING_FLOAT32_LE;
    frame->layout = aetus_ingest_v1_SignalSampleLayout_SIGNAL_SAMPLE_LAYOUT_INTERLEAVED;
    frame->channels_count = 3;

    strncpy(frame->channels[0].key, "accel_x", sizeof(frame->channels[0].key) - 1);
    strncpy(frame->channels[0].unit, "g", sizeof(frame->channels[0].unit) - 1);
    strncpy(frame->channels[1].key, "accel_y", sizeof(frame->channels[1].key) - 1);
    strncpy(frame->channels[1].unit, "g", sizeof(frame->channels[1].unit) - 1);
    strncpy(frame->channels[2].key, "accel_z", sizeof(frame->channels[2].key) - 1);
    strncpy(frame->channels[2].unit, "g", sizeof(frame->channels[2].unit) - 1);

    frame->samples.size = sizeof(samples);
    memcpy(frame->samples.bytes, samples, sizeof(samples));

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}
