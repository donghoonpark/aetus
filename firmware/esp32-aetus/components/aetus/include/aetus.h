#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

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
#define AETUS_UPLOAD_DEFAULT_INTERVAL_MS (10U * 60U * 1000U)

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
    AETUS_DEVICE_STATUS_ONLINE = 0,
    AETUS_DEVICE_STATUS_DEGRADED = 1,
    AETUS_DEVICE_STATUS_OFFLINE = 2,
} aetus_device_status_t;

typedef struct {
    aetus_device_status_t status;
    int32_t rssi;
    uint32_t free_heap;
    char reboot_reason[24];
    uint64_t timestamp_ns;
} aetus_status_t;

typedef struct {
    const char *wifi_ssid;
    const char *wifi_password;
    const char *ingest_url;
    const char *device_id;
    const char *device_token;
    uint32_t firmware_version;
    uint32_t upload_interval_ms;
    uint32_t queue_depth;
} aetus_config_t;

esp_err_t aetus_start(const aetus_config_t *config);
esp_err_t aetus_enqueue_telemetry(const aetus_telemetry_t *telemetry, TickType_t timeout);
esp_err_t aetus_enqueue_status(const aetus_status_t *status, TickType_t timeout);
esp_err_t aetus_flush(TickType_t timeout);

#ifdef __cplusplus
}
#endif
