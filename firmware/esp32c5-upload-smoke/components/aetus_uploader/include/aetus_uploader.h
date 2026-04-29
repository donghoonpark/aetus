#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AETUS_MAX_METRICS 8
#define AETUS_UPLOAD_DEFAULT_INTERVAL_MS (10U * 60U * 1000U)

typedef struct {
    char key[24];
    double value;
    char unit[16];
} aetus_metric_t;

typedef struct {
    uint64_t timestamp_ns;
    uint32_t metric_count;
    aetus_metric_t metrics[AETUS_MAX_METRICS];
} aetus_upload_message_t;

typedef struct {
    const char *wifi_ssid;
    const char *wifi_password;
    const char *ingest_url;
    const char *device_id;
    const char *device_token;
    uint32_t firmware_version;
    uint32_t upload_interval_ms;
    uint32_t queue_depth;
} aetus_uploader_config_t;

esp_err_t aetus_uploader_start(const aetus_uploader_config_t *config);
esp_err_t aetus_uploader_enqueue(const aetus_upload_message_t *message, TickType_t timeout);
esp_err_t aetus_uploader_flush(TickType_t timeout);

#ifdef __cplusplus
}
#endif
