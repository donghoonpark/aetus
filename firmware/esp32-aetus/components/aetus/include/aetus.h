#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#if defined(__has_include)
#if __has_include("sdkconfig.h")
#include "sdkconfig.h"
#endif
#endif

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AETUS_MAX_METRICS 8
#define AETUS_METRIC_KEY_MAX 24
#define AETUS_METRIC_UNIT_MAX 16
#define AETUS_METRIC_STRING_MAX 64
#define AETUS_METRIC_BYTES_MAX 64
#define AETUS_SIGNAL_STREAM_KEY_MAX 32
#define AETUS_SIGNAL_CHANNELS_MAX 4
#ifndef AETUS_SIGNAL_SAMPLES_MAX
#ifdef CONFIG_AETUS_SIGNAL_SAMPLES_MAX
#define AETUS_SIGNAL_SAMPLES_MAX CONFIG_AETUS_SIGNAL_SAMPLES_MAX
#else
#define AETUS_SIGNAL_SAMPLES_MAX 2400
#endif
#endif
#ifndef AETUS_SIGNAL_SAMPLES_ABSOLUTE_MAX
#define AETUS_SIGNAL_SAMPLES_ABSOLUTE_MAX 8192
#endif
#ifndef AETUS_SIGNAL_FRAME_STRUCT_MAX_BYTES
#define AETUS_SIGNAL_FRAME_STRUCT_MAX_BYTES 512
#endif
#define AETUS_WIFI_SSID_MAX 32
#define AETUS_WIFI_PASSWORD_MAX 64
#define AETUS_WIFI_IDENTITY_MAX 127
#define AETUS_URL_MAX 159
#define AETUS_DEVICE_ID_MAX 63
#define AETUS_DEVICE_TOKEN_MAX 127
#define AETUS_UPLOAD_DEFAULT_INTERVAL_MS (10U * 60U * 1000U)
#define AETUS_RTC_VALID_AFTER_UNIX_S 1577836800ULL

#ifdef __cplusplus
#define AETUS_STATIC_ASSERT(condition, message) static_assert((condition), message)
#else
#define AETUS_STATIC_ASSERT(condition, message) _Static_assert((condition), message)
#endif

AETUS_STATIC_ASSERT(AETUS_SIGNAL_SAMPLES_MAX > 0, "AETUS_SIGNAL_SAMPLES_MAX must be greater than zero");
AETUS_STATIC_ASSERT(
    AETUS_SIGNAL_SAMPLES_MAX <= AETUS_SIGNAL_SAMPLES_ABSOLUTE_MAX,
    "AETUS_SIGNAL_SAMPLES_MAX exceeds the supported static frame limit"
);

typedef enum {
    AETUS_METRIC_VALUE_INT64 = 0,
    AETUS_METRIC_VALUE_DOUBLE = 1,
    AETUS_METRIC_VALUE_BOOL = 2,
    AETUS_METRIC_VALUE_STRING = 3,
    AETUS_METRIC_VALUE_BYTES = 4,
} aetus_metric_value_type_t;

typedef struct {
    uint8_t data[AETUS_METRIC_BYTES_MAX];
    size_t size;
} aetus_metric_bytes_t;

typedef struct {
    char key[AETUS_METRIC_KEY_MAX];
    aetus_metric_value_type_t type;
    union {
        int64_t int64_value;
        double double_value;
        bool bool_value;
        char string_value[AETUS_METRIC_STRING_MAX];
        aetus_metric_bytes_t bytes_value;
    } value;
    char unit[AETUS_METRIC_UNIT_MAX];
} aetus_metric_t;

typedef struct {
    uint64_t timestamp_ns;
    uint32_t metric_count;
    aetus_metric_t metrics[AETUS_MAX_METRICS];
} aetus_telemetry_t;

typedef enum {
    AETUS_SIGNAL_ENCODING_FLOAT32_LE = 0,
    AETUS_SIGNAL_ENCODING_INT16_LE = 1,
    AETUS_SIGNAL_ENCODING_UINT16_LE = 2,
    AETUS_SIGNAL_ENCODING_INT32_LE = 3,
} aetus_signal_encoding_t;

typedef enum {
    AETUS_SIGNAL_LAYOUT_INTERLEAVED = 0,
    AETUS_SIGNAL_LAYOUT_PLANAR = 1,
} aetus_signal_layout_t;

typedef enum {
    AETUS_SIGNAL_SAMPLE_POOL_STATIC = 0,
    AETUS_SIGNAL_SAMPLE_POOL_FREERTOS_HEAP = 1,
} aetus_signal_sample_pool_backend_t;

typedef struct {
    char key[AETUS_METRIC_KEY_MAX];
    char unit[AETUS_METRIC_UNIT_MAX];
    bool has_scale;
    float scale;
    bool has_offset;
    float offset;
} aetus_signal_channel_t;

typedef struct {
    uint64_t timestamp_ns;
    char stream_key[AETUS_SIGNAL_STREAM_KEY_MAX];
    uint64_t sample_interval_ns;
    uint32_t sample_count;
    aetus_signal_encoding_t encoding;
    aetus_signal_layout_t layout;
    uint32_t channel_count;
    aetus_signal_channel_t channels[AETUS_SIGNAL_CHANNELS_MAX];
    const uint8_t *samples;
    size_t samples_size;
} aetus_signal_frame_t;

AETUS_STATIC_ASSERT(
    sizeof(aetus_signal_frame_t) <= AETUS_SIGNAL_FRAME_STRUCT_MAX_BYTES,
    "aetus_signal_frame_t is too large for the configured static memory budget"
);

typedef enum {
    AETUS_DEVICE_STATUS_ONLINE = 0,
    AETUS_DEVICE_STATUS_DEGRADED = 1,
    AETUS_DEVICE_STATUS_OFFLINE = 2,
} aetus_device_status_t;

typedef enum {
    AETUS_WIFI_AUTH_PSK = 0,
    AETUS_WIFI_AUTH_PEAP = 1,
} aetus_wifi_auth_t;

typedef enum {
    AETUS_AUTH_BEARER = 0,
    AETUS_AUTH_HMAC_SHA256 = 1,
} aetus_auth_mode_t;

typedef struct {
    aetus_device_status_t status;
    int32_t rssi;
    uint32_t free_heap;
    char reboot_reason[24];
    uint64_t timestamp_ns;
} aetus_status_t;

typedef struct {
    uint32_t allocated_blocks;
    uint32_t peak_allocated_blocks;
    uint32_t allocation_count;
    uint32_t release_count;
    uint32_t allocation_failure_count;
    uint32_t queue_send_failure_release_count;
    uint32_t validation_failure_release_count;
    uint32_t upload_success_release_count;
    uint32_t final_drop_release_count;
    size_t allocated_bytes;
    size_t peak_allocated_bytes;
} aetus_signal_sample_pool_stats_t;

typedef struct {
    const char *wifi_ssid;
    const char *wifi_password;
    aetus_wifi_auth_t wifi_auth;
    const char *wifi_identity;
    const char *ingest_url;
    const char *time_url;
    const char *device_id;
    const char *device_token;
    aetus_auth_mode_t auth_mode;
    uint32_t firmware_version;
    uint32_t upload_interval_ms;
    uint32_t queue_depth;
    aetus_signal_sample_pool_backend_t signal_sample_pool_backend;
    bool connected_led_enabled;
    int connected_led_gpio;
} aetus_config_t;

typedef void (*aetus_provisioning_config_changed_cb_t)(const aetus_config_t *config, void *user_ctx);
typedef void (*aetus_provisioning_connection_check_cb_t)(
    uint16_t conn_handle,
    int status,
    uint16_t interval,
    uint16_t latency,
    uint16_t supervision_timeout,
    void *user_ctx
);

typedef struct {
    const char *device_name;
    aetus_provisioning_config_changed_cb_t config_changed_cb;
    aetus_provisioning_connection_check_cb_t connection_check_cb;
    void *user_ctx;
} aetus_provisioning_config_t;

void aetus_telemetry_init(aetus_telemetry_t *telemetry);
void aetus_signal_frame_init(aetus_signal_frame_t *frame);
void aetus_status_init(aetus_status_t *status, aetus_device_status_t device_status);
esp_err_t aetus_status_set_reboot_reason(aetus_status_t *status, const char *reboot_reason);
esp_err_t aetus_rtc_timestamp_ns(uint64_t *timestamp_ns);
esp_err_t aetus_telemetry_set_timestamp_rtc(aetus_telemetry_t *telemetry);
esp_err_t aetus_signal_frame_set_timestamp_rtc(aetus_signal_frame_t *frame);
esp_err_t aetus_status_set_timestamp_rtc(aetus_status_t *status);
esp_err_t aetus_telemetry_add_int64(
    aetus_telemetry_t *telemetry,
    const char *key,
    int64_t value,
    const char *unit
);
esp_err_t aetus_telemetry_add_double(
    aetus_telemetry_t *telemetry,
    const char *key,
    double value,
    const char *unit
);
esp_err_t aetus_telemetry_add_bool(
    aetus_telemetry_t *telemetry,
    const char *key,
    bool value,
    const char *unit
);
esp_err_t aetus_telemetry_add_string(
    aetus_telemetry_t *telemetry,
    const char *key,
    const char *value,
    const char *unit
);
esp_err_t aetus_telemetry_add_bytes(
    aetus_telemetry_t *telemetry,
    const char *key,
    const uint8_t *value,
    size_t value_size,
    const char *unit
);
esp_err_t aetus_signal_frame_set_stream_key(aetus_signal_frame_t *frame, const char *stream_key);
esp_err_t aetus_signal_frame_add_channel(
    aetus_signal_frame_t *frame,
    const char *key,
    const char *unit,
    const float *scale,
    const float *offset
);
esp_err_t aetus_signal_frame_set_samples(aetus_signal_frame_t *frame, const void *samples, size_t samples_size);
esp_err_t aetus_start(const aetus_config_t *config);
esp_err_t aetus_update_config(const aetus_config_t *config);
esp_err_t aetus_get_config(aetus_config_t *config);
esp_err_t aetus_get_signal_sample_pool_stats(aetus_signal_sample_pool_stats_t *stats);
esp_err_t aetus_start_provisioning(const aetus_provisioning_config_t *config);
esp_err_t aetus_sync_rtc(TickType_t timeout);
esp_err_t aetus_enqueue_telemetry(const aetus_telemetry_t *telemetry, TickType_t timeout);
esp_err_t aetus_enqueue_signal_frame(const aetus_signal_frame_t *frame, TickType_t timeout);
esp_err_t aetus_enqueue_status(const aetus_status_t *status, TickType_t timeout);
esp_err_t aetus_flush(TickType_t timeout);
esp_err_t aetus_enqueue_telemetry_from_isr(
    const aetus_telemetry_t *telemetry,
    BaseType_t *pxHigherPriorityTaskWoken
);
esp_err_t aetus_enqueue_status_from_isr(
    const aetus_status_t *status,
    BaseType_t *pxHigherPriorityTaskWoken
);

#ifdef __cplusplus
}
#endif
