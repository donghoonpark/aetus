#include "aetus.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_eap_client.h"
#include "driver/gpio.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "nvs_flash.h"
#include "pb_encode.h"

#include "ingest.pb.h"

#define AETUS_WIFI_CONNECTED_BIT BIT0
#define AETUS_UPLOAD_DUE_BIT BIT1
#define AETUS_UPLOAD_FLUSH_BIT BIT2
#define AETUS_UPLOAD_DONE_BIT BIT3
#define AETUS_HTTP_TIMEOUT_MS 10000
#define AETUS_TASK_STACK_BYTES 8192
#define AETUS_TASK_PRIORITY 5
#define AETUS_WIFI_CONNECT_TIMEOUT_MS 15000
#define AETUS_ENCODE_BUFFER_BYTES 1024
#define AETUS_TIME_RESPONSE_BUFFER_BYTES 512
#define AETUS_INGEST_PATH "/v1/ingest"
#define AETUS_TIME_PATH "/v1/time"

static const char *TAG = "aetus";

typedef enum {
    AETUS_QUEUE_ITEM_TELEMETRY = 0,
    AETUS_QUEUE_ITEM_STATUS = 1,
} aetus_queue_item_kind_t;

typedef struct {
    aetus_queue_item_kind_t kind;
    union {
        aetus_telemetry_t telemetry;
        aetus_status_t status;
    } body;
} aetus_queue_item_t;

typedef struct {
    const uint8_t *data;
    size_t size;
} aetus_bytes_arg_t;

typedef struct {
    char *data;
    size_t capacity;
    size_t length;
    bool overflow;
} aetus_http_body_t;

typedef struct {
    aetus_config_t config;
    QueueHandle_t queue;
    EventGroupHandle_t events;
    TimerHandle_t upload_timer;
    uint64_t sequence;
    char boot_id[32];
    bool wifi_started;
} aetus_ctx_t;

static aetus_ctx_t s_ctx;

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }

    if (source == NULL) {
        target[0] = '\0';
        return;
    }

    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static bool string_required_fits(const char *value, size_t max_len)
{
    return value != NULL && value[0] != '\0' && strlen(value) <= max_len;
}

static bool string_fits(const char *value, size_t target_size)
{
    if (value == NULL) {
        return true;
    }
    if (target_size == 0) {
        return false;
    }
    return strnlen(value, target_size) < target_size;
}

static bool ends_with(const char *value, const char *suffix)
{
    if (value == NULL || suffix == NULL) {
        return false;
    }
    size_t value_len = strlen(value);
    size_t suffix_len = strlen(suffix);
    return value_len >= suffix_len && memcmp(value + value_len - suffix_len, suffix, suffix_len) == 0;
}

static bool encode_string_callback(pb_ostream_t *stream, const pb_field_t *field, void *const *arg)
{
    const char *value = (const char *)(*arg);
    if (value == NULL) {
        value = "";
    }
    if (!pb_encode_tag_for_field(stream, field)) {
        return false;
    }
    return pb_encode_string(stream, (const pb_byte_t *)value, strlen(value));
}

static bool encode_bytes_callback(pb_ostream_t *stream, const pb_field_t *field, void *const *arg)
{
    const aetus_bytes_arg_t *value = (const aetus_bytes_arg_t *)(*arg);
    if (value == NULL || value->data == NULL) {
        return false;
    }
    if (!pb_encode_tag_for_field(stream, field)) {
        return false;
    }
    return pb_encode_string(stream, (const pb_byte_t *)value->data, value->size);
}

static aetus_ingest_v1_DeviceStatus map_device_status(aetus_device_status_t status)
{
    switch (status) {
    case AETUS_DEVICE_STATUS_ONLINE:
        return aetus_ingest_v1_DeviceStatus_DEVICE_STATUS_ONLINE;
    case AETUS_DEVICE_STATUS_DEGRADED:
        return aetus_ingest_v1_DeviceStatus_DEVICE_STATUS_DEGRADED;
    case AETUS_DEVICE_STATUS_OFFLINE:
        return aetus_ingest_v1_DeviceStatus_DEVICE_STATUS_OFFLINE;
    default:
        return aetus_ingest_v1_DeviceStatus_DEVICE_STATUS_UNSPECIFIED;
    }
}

static void wifi_event_handler(
    void *arg,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
)
{
    aetus_ctx_t *ctx = (aetus_ctx_t *)arg;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(ctx->events, AETUS_WIFI_CONNECTED_BIT);
        if (ctx->config.connected_led_enabled) {
            gpio_set_level((gpio_num_t)ctx->config.connected_led_gpio, 0);
        }
        ESP_LOGW(TAG, "wifi disconnected, reconnecting");
        esp_wifi_connect();
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "wifi got ip " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(ctx->events, AETUS_WIFI_CONNECTED_BIT);
        if (ctx->config.connected_led_enabled) {
            gpio_set_level((gpio_num_t)ctx->config.connected_led_gpio, 1);
        }
    }
}

static esp_err_t ok_if_already_initialized(esp_err_t err)
{
    return err == ESP_ERR_INVALID_STATE ? ESP_OK : err;
}

static esp_err_t wifi_configure_peap(const aetus_config_t *config)
{
    const int identity_len = (int)strlen(config->wifi_identity);
    const int password_len = (int)strlen(config->wifi_password);

    ESP_RETURN_ON_ERROR(
        esp_eap_client_set_identity((const unsigned char *)config->wifi_identity, identity_len),
        TAG,
        "wifi peap identity failed"
    );
    ESP_RETURN_ON_ERROR(
        esp_eap_client_set_username((const unsigned char *)config->wifi_identity, identity_len),
        TAG,
        "wifi peap username failed"
    );
    ESP_RETURN_ON_ERROR(
        esp_eap_client_set_password((const unsigned char *)config->wifi_password, password_len),
        TAG,
        "wifi peap password failed"
    );
    ESP_RETURN_ON_ERROR(
        esp_eap_client_set_eap_methods(ESP_EAP_TYPE_PEAP),
        TAG,
        "wifi peap method failed"
    );
    ESP_RETURN_ON_ERROR(esp_wifi_sta_enterprise_enable(), TAG, "wifi peap enable failed");
    ESP_LOGI(TAG, "wifi auth configured: PEAP");
    return ESP_OK;
}

static esp_err_t configure_connected_led(aetus_ctx_t *ctx)
{
    if (!ctx->config.connected_led_enabled) {
        return ESP_OK;
    }

    gpio_config_t io_config = {
        .pin_bit_mask = 1ULL << ctx->config.connected_led_gpio,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&io_config), TAG, "connected led gpio config failed");

    EventBits_t bits = ctx->events != NULL ? xEventGroupGetBits(ctx->events) : 0;
    gpio_set_level(
        (gpio_num_t)ctx->config.connected_led_gpio,
        (bits & AETUS_WIFI_CONNECTED_BIT) ? 1 : 0
    );
    return ESP_OK;
}

static esp_err_t wifi_apply_sta_config(aetus_ctx_t *ctx)
{
    wifi_config_t wifi_config = {0};
    copy_string((char *)wifi_config.sta.ssid, sizeof(wifi_config.sta.ssid), ctx->config.wifi_ssid);
    if (ctx->config.wifi_auth == AETUS_WIFI_AUTH_PSK) {
        copy_string((char *)wifi_config.sta.password, sizeof(wifi_config.sta.password), ctx->config.wifi_password);
        wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
        wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
        esp_wifi_sta_enterprise_disable();
    } else if (ctx->config.wifi_auth == AETUS_WIFI_AUTH_PEAP) {
        wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_ENTERPRISE;
    }

    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config), TAG, "wifi config failed");
    if (ctx->config.wifi_auth == AETUS_WIFI_AUTH_PEAP) {
        ESP_RETURN_ON_ERROR(wifi_configure_peap(&ctx->config), TAG, "wifi peap config failed");
    }
    return ESP_OK;
}

static esp_err_t wifi_start(aetus_ctx_t *ctx)
{
    if (ctx->wifi_started) {
        return ESP_OK;
    }

    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES || nvs_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "nvs erase failed");
        nvs_err = nvs_flash_init();
    }
    ESP_RETURN_ON_ERROR(nvs_err, TAG, "nvs init failed");
    ESP_RETURN_ON_ERROR(ok_if_already_initialized(esp_netif_init()), TAG, "netif init failed");
    ESP_RETURN_ON_ERROR(
        ok_if_already_initialized(esp_event_loop_create_default()),
        TAG,
        "event loop init failed"
    );
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t wifi_init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&wifi_init), TAG, "wifi init failed");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(
            WIFI_EVENT,
            ESP_EVENT_ANY_ID,
            &wifi_event_handler,
            ctx,
            NULL
        ),
        TAG,
        "wifi event handler failed"
    );
    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(
            IP_EVENT,
            IP_EVENT_STA_GOT_IP,
            &wifi_event_handler,
            ctx,
            NULL
        ),
        TAG,
        "ip event handler failed"
    );

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "wifi mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "wifi storage failed");
    ESP_RETURN_ON_ERROR(wifi_apply_sta_config(ctx), TAG, "wifi sta config failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start failed");
    ctx->wifi_started = true;
    return ESP_OK;
}

static esp_err_t wifi_wait_connected_for(aetus_ctx_t *ctx, TickType_t timeout)
{
    ESP_RETURN_ON_ERROR(wifi_start(ctx), TAG, "wifi start failed");
    EventBits_t bits = xEventGroupWaitBits(
        ctx->events,
        AETUS_WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        timeout
    );
    return (bits & AETUS_WIFI_CONNECTED_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

static esp_err_t wifi_wait_connected(aetus_ctx_t *ctx)
{
    return wifi_wait_connected_for(ctx, pdMS_TO_TICKS(AETUS_WIFI_CONNECT_TIMEOUT_MS));
}

static esp_err_t collect_http_body(esp_http_client_event_t *event)
{
    if (event->event_id != HTTP_EVENT_ON_DATA || event->user_data == NULL || event->data == NULL) {
        return ESP_OK;
    }

    aetus_http_body_t *body = (aetus_http_body_t *)event->user_data;
    size_t incoming = (size_t)event->data_len;
    if (body->length + incoming >= body->capacity) {
        body->overflow = true;
        incoming = body->capacity - body->length - 1;
    }
    if (incoming > 0) {
        memcpy(body->data + body->length, event->data, incoming);
        body->length += incoming;
        body->data[body->length] = '\0';
    }
    return ESP_OK;
}

static esp_err_t resolve_time_url(const aetus_ctx_t *ctx, char *buffer, size_t buffer_size, const char **time_url)
{
    ESP_RETURN_ON_FALSE(ctx != NULL, ESP_ERR_INVALID_ARG, TAG, "context is required");
    ESP_RETURN_ON_FALSE(time_url != NULL, ESP_ERR_INVALID_ARG, TAG, "time url output is required");

    if (ctx->config.time_url != NULL && ctx->config.time_url[0] != '\0') {
        *time_url = ctx->config.time_url;
        return ESP_OK;
    }

    const char *ingest_url = ctx->config.ingest_url;
    ESP_RETURN_ON_FALSE(
        ends_with(ingest_url, AETUS_INGEST_PATH),
        ESP_ERR_INVALID_STATE,
        TAG,
        "time_url is required when ingest_url does not end with /v1/ingest"
    );

    size_t prefix_len = strlen(ingest_url) - strlen(AETUS_INGEST_PATH);
    size_t required = prefix_len + strlen(AETUS_TIME_PATH) + 1;
    ESP_RETURN_ON_FALSE(required <= buffer_size, ESP_ERR_NO_MEM, TAG, "time url buffer too small");
    memcpy(buffer, ingest_url, prefix_len);
    memcpy(buffer + prefix_len, AETUS_TIME_PATH, strlen(AETUS_TIME_PATH) + 1);
    *time_url = buffer;
    return ESP_OK;
}

static esp_err_t parse_unix_time_ns(const char *body, uint64_t *unix_time_ns)
{
    ESP_RETURN_ON_FALSE(body != NULL, ESP_ERR_INVALID_ARG, TAG, "time response body is required");
    ESP_RETURN_ON_FALSE(unix_time_ns != NULL, ESP_ERR_INVALID_ARG, TAG, "time output is required");

    const char *key = "\"unix_time_ns\"";
    const char *cursor = strstr(body, key);
    ESP_RETURN_ON_FALSE(cursor != NULL, ESP_ERR_INVALID_RESPONSE, TAG, "unix_time_ns missing");
    cursor = strchr(cursor + strlen(key), ':');
    ESP_RETURN_ON_FALSE(cursor != NULL, ESP_ERR_INVALID_RESPONSE, TAG, "unix_time_ns separator missing");
    cursor++;

    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\r' || *cursor == '\n') {
        cursor++;
    }
    if (*cursor == '"') {
        cursor++;
    }

    errno = 0;
    char *end = NULL;
    unsigned long long parsed = strtoull(cursor, &end, 10);
    ESP_RETURN_ON_FALSE(errno == 0 && end != cursor, ESP_ERR_INVALID_RESPONSE, TAG, "unix_time_ns invalid");

    *unix_time_ns = (uint64_t)parsed;
    return ESP_OK;
}

static esp_err_t fetch_server_time_ns(aetus_ctx_t *ctx, uint64_t *unix_time_ns)
{
    char time_url_buffer[160] = {0};
    const char *time_url = NULL;
    ESP_RETURN_ON_ERROR(resolve_time_url(ctx, time_url_buffer, sizeof(time_url_buffer), &time_url), TAG, "time url failed");

    char response_body[AETUS_TIME_RESPONSE_BUFFER_BYTES] = {0};
    aetus_http_body_t body = {
        .data = response_body,
        .capacity = sizeof(response_body),
        .length = 0,
        .overflow = false,
    };
    esp_http_client_config_t http_config = {
        .url = time_url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = AETUS_HTTP_TIMEOUT_MS,
        .event_handler = collect_http_body,
        .user_data = &body,
    };
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (client == NULL) {
        return ESP_FAIL;
    }

    char authorization[128];
    snprintf(authorization, sizeof(authorization), "Bearer %s", ctx->config.device_token);
    esp_http_client_set_header(client, "X-Device-Id", ctx->config.device_id);
    esp_http_client_set_header(client, "Authorization", authorization);

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "time sync http failed: %s", esp_err_to_name(err));
        return err;
    }
    if (status < 200 || status >= 300) {
        ESP_LOGE(TAG, "time sync rejected status=%d", status);
        return ESP_FAIL;
    }
    ESP_RETURN_ON_FALSE(!body.overflow, ESP_ERR_NO_MEM, TAG, "time response too large");
    return parse_unix_time_ns(response_body, unix_time_ns);
}

static esp_err_t set_rtc_from_unix_time_ns(uint64_t unix_time_ns)
{
    uint64_t unix_time_s = unix_time_ns / 1000000000ULL;
    ESP_RETURN_ON_FALSE(
        unix_time_s >= AETUS_RTC_VALID_AFTER_UNIX_S,
        ESP_ERR_INVALID_RESPONSE,
        TAG,
        "server time is before valid rtc threshold"
    );

    struct timeval tv = {
        .tv_sec = (time_t)unix_time_s,
        .tv_usec = (suseconds_t)((unix_time_ns % 1000000000ULL) / 1000ULL),
    };
    ESP_RETURN_ON_FALSE(settimeofday(&tv, NULL) == 0, ESP_FAIL, TAG, "rtc write failed");
    return ESP_OK;
}

static void fill_event_header(aetus_ctx_t *ctx, aetus_ingest_v1_IngestEvent *event)
{
    event->schema_version = 1;
    copy_string(event->device_id, sizeof(event->device_id), ctx->config.device_id);
    event->sequence = ctx->sequence;
    copy_string(event->boot_id, sizeof(event->boot_id), ctx->boot_id);
    event->firmware_version = ctx->config.firmware_version;
    event->uptime_ms = (uint64_t)(esp_timer_get_time() / 1000);
}

static void fill_metric(
    aetus_ingest_v1_Metric *target,
    const aetus_metric_t *source,
    aetus_bytes_arg_t *bytes_arg
)
{
    copy_string(target->key, sizeof(target->key), source->key);
    copy_string(target->unit, sizeof(target->unit), source->unit);

    switch (source->type) {
    case AETUS_METRIC_VALUE_INT64:
        target->which_value = aetus_ingest_v1_Metric_int_value_tag;
        target->value.int_value = source->value.int64_value;
        break;
    case AETUS_METRIC_VALUE_BOOL:
        target->which_value = aetus_ingest_v1_Metric_bool_value_tag;
        target->value.bool_value = source->value.bool_value;
        break;
    case AETUS_METRIC_VALUE_STRING:
        target->which_value = aetus_ingest_v1_Metric_string_value_tag;
        target->value.string_value.funcs.encode = encode_string_callback;
        target->value.string_value.arg = (void *)source->value.string_value;
        break;
    case AETUS_METRIC_VALUE_BYTES:
        bytes_arg->data = source->value.bytes_value.data;
        bytes_arg->size = source->value.bytes_value.size;
        if (bytes_arg->size > AETUS_METRIC_BYTES_MAX) {
            bytes_arg->size = AETUS_METRIC_BYTES_MAX;
        }
        target->which_value = aetus_ingest_v1_Metric_bytes_value_tag;
        target->value.bytes_value.funcs.encode = encode_bytes_callback;
        target->value.bytes_value.arg = bytes_arg;
        break;
    case AETUS_METRIC_VALUE_DOUBLE:
    default:
        target->which_value = aetus_ingest_v1_Metric_double_value_tag;
        target->value.double_value = source->value.double_value;
        break;
    }
}

static bool encode_telemetry(
    aetus_ctx_t *ctx,
    const aetus_telemetry_t *telemetry,
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size
)
{
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    aetus_bytes_arg_t bytes_args[AETUS_MAX_METRICS] = {0};
    fill_event_header(ctx, &event);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    event.timestamp_ns = telemetry->timestamp_ns;
    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;

    uint32_t metric_count = telemetry->metric_count;
    if (metric_count > AETUS_MAX_METRICS) {
        metric_count = AETUS_MAX_METRICS;
    }
    event.body.telemetry.metrics_count = metric_count;

    for (uint32_t index = 0; index < metric_count; index++) {
        fill_metric(&event.body.telemetry.metrics[index], &telemetry->metrics[index], &bytes_args[index]);
    }

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        ESP_LOGE(TAG, "protobuf telemetry encode failed");
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

static bool encode_status(
    aetus_ctx_t *ctx,
    const aetus_status_t *status,
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size
)
{
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    fill_event_header(ctx, &event);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_STATUS;
    event.timestamp_ns = status->timestamp_ns;
    event.which_body = aetus_ingest_v1_IngestEvent_status_tag;
    event.body.status.status = map_device_status(status->status);
    event.body.status.rssi = status->rssi;
    event.body.status.free_heap = status->free_heap;
    copy_string(event.body.status.reboot_reason, sizeof(event.body.status.reboot_reason), status->reboot_reason);

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        ESP_LOGE(TAG, "protobuf status encode failed");
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

static bool encode_queue_item(
    aetus_ctx_t *ctx,
    const aetus_queue_item_t *item,
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size
)
{
    if (item->kind == AETUS_QUEUE_ITEM_STATUS) {
        return encode_status(ctx, &item->body.status, buffer, buffer_size, encoded_size);
    }
    return encode_telemetry(ctx, &item->body.telemetry, buffer, buffer_size, encoded_size);
}

static esp_err_t post_payload(aetus_ctx_t *ctx, const uint8_t *payload, size_t payload_size)
{
    esp_http_client_config_t http_config = {
        .url = ctx->config.ingest_url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = AETUS_HTTP_TIMEOUT_MS,
    };
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (client == NULL) {
        return ESP_FAIL;
    }

    char authorization[128];
    snprintf(authorization, sizeof(authorization), "Bearer %s", ctx->config.device_token);
    esp_http_client_set_header(client, "Content-Type", "application/x-protobuf");
    esp_http_client_set_header(client, "X-Device-Id", ctx->config.device_id);
    esp_http_client_set_header(client, "Authorization", authorization);
    esp_http_client_set_post_field(client, (const char *)payload, payload_size);

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    int content_length = esp_http_client_get_content_length(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "http post failed: %s", esp_err_to_name(err));
        return err;
    }
    if (status < 200 || status >= 300) {
        ESP_LOGE(TAG, "http post rejected status=%d content_length=%d", status, content_length);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "AETUS_UPLOAD_OK sequence=%llu status=%d bytes=%u", ctx->sequence, status, (unsigned)payload_size);
    return ESP_OK;
}

static void drain_queue(aetus_ctx_t *ctx)
{
    esp_err_t err = wifi_wait_connected(ctx);
    uint32_t uploaded = 0;
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi not connected before upload: %s", esp_err_to_name(err));
        xEventGroupSetBits(ctx->events, AETUS_UPLOAD_DONE_BIT);
        return;
    }

    aetus_queue_item_t item;
    while (xQueueReceive(ctx->queue, &item, 0) == pdTRUE) {
        uint8_t payload[AETUS_ENCODE_BUFFER_BYTES];
        size_t payload_size = 0;
        if (!encode_queue_item(ctx, &item, payload, sizeof(payload), &payload_size)) {
            continue;
        }

        err = post_payload(ctx, payload, payload_size);
        if (err == ESP_OK) {
            ctx->sequence++;
            uploaded++;
            continue;
        }

        ESP_LOGW(TAG, "AETUS_UPLOAD_FAILED sequence=%llu; requeueing", ctx->sequence);
        if (xQueueSendToFront(ctx->queue, &item, 0) != pdTRUE) {
            ESP_LOGE(TAG, "failed message dropped because queue is full");
        }
        break;
    }

    if (uploaded == 0) {
        ESP_LOGI(TAG, "AETUS_UPLOAD_EMPTY");
    }
    xEventGroupSetBits(ctx->events, AETUS_UPLOAD_DONE_BIT);
}

static void upload_timer_callback(TimerHandle_t timer)
{
    aetus_ctx_t *ctx = (aetus_ctx_t *)pvTimerGetTimerID(timer);
    xEventGroupSetBits(ctx->events, AETUS_UPLOAD_DUE_BIT);
}

static void uploader_task(void *arg)
{
    aetus_ctx_t *ctx = (aetus_ctx_t *)arg;
    while (true) {
        EventBits_t bits = xEventGroupWaitBits(
            ctx->events,
            AETUS_UPLOAD_DUE_BIT | AETUS_UPLOAD_FLUSH_BIT,
            pdTRUE,
            pdFALSE,
            portMAX_DELAY
        );
        if (bits & (AETUS_UPLOAD_DUE_BIT | AETUS_UPLOAD_FLUSH_BIT)) {
            drain_queue(ctx);
        }
    }
}

void aetus_telemetry_init(aetus_telemetry_t *telemetry)
{
    if (telemetry == NULL) {
        return;
    }
    memset(telemetry, 0, sizeof(*telemetry));
}

void aetus_status_init(aetus_status_t *status, aetus_device_status_t device_status)
{
    if (status == NULL) {
        return;
    }
    memset(status, 0, sizeof(*status));
    status->status = device_status;
}

esp_err_t aetus_status_set_reboot_reason(aetus_status_t *status, const char *reboot_reason)
{
    ESP_RETURN_ON_FALSE(status != NULL, ESP_ERR_INVALID_ARG, TAG, "status is required");
    ESP_RETURN_ON_FALSE(reboot_reason != NULL, ESP_ERR_INVALID_ARG, TAG, "reboot reason is required");
    ESP_RETURN_ON_FALSE(
        string_fits(reboot_reason, sizeof(status->reboot_reason)),
        ESP_ERR_INVALID_ARG,
        TAG,
        "reboot reason too long"
    );
    copy_string(status->reboot_reason, sizeof(status->reboot_reason), reboot_reason);
    return ESP_OK;
}

esp_err_t aetus_rtc_timestamp_ns(uint64_t *timestamp_ns)
{
    ESP_RETURN_ON_FALSE(timestamp_ns != NULL, ESP_ERR_INVALID_ARG, TAG, "timestamp output is required");

    struct timeval now;
    ESP_RETURN_ON_FALSE(gettimeofday(&now, NULL) == 0, ESP_FAIL, TAG, "rtc read failed");
    ESP_RETURN_ON_FALSE(
        (uint64_t)now.tv_sec >= AETUS_RTC_VALID_AFTER_UNIX_S,
        ESP_ERR_INVALID_STATE,
        TAG,
        "rtc is not initialized"
    );

    *timestamp_ns = ((uint64_t)now.tv_sec * 1000000000ULL) + ((uint64_t)now.tv_usec * 1000ULL);
    return ESP_OK;
}

esp_err_t aetus_telemetry_set_timestamp_rtc(aetus_telemetry_t *telemetry)
{
    ESP_RETURN_ON_FALSE(telemetry != NULL, ESP_ERR_INVALID_ARG, TAG, "telemetry is required");
    return aetus_rtc_timestamp_ns(&telemetry->timestamp_ns);
}

esp_err_t aetus_status_set_timestamp_rtc(aetus_status_t *status)
{
    ESP_RETURN_ON_FALSE(status != NULL, ESP_ERR_INVALID_ARG, TAG, "status is required");
    return aetus_rtc_timestamp_ns(&status->timestamp_ns);
}

static esp_err_t append_metric(
    aetus_telemetry_t *telemetry,
    const char *key,
    const char *unit,
    aetus_metric_t **metric
)
{
    ESP_RETURN_ON_FALSE(telemetry != NULL, ESP_ERR_INVALID_ARG, TAG, "telemetry is required");
    ESP_RETURN_ON_FALSE(key != NULL && key[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "metric key is required");
    ESP_RETURN_ON_FALSE(metric != NULL, ESP_ERR_INVALID_ARG, TAG, "metric output is required");
    ESP_RETURN_ON_FALSE(string_fits(key, AETUS_METRIC_KEY_MAX), ESP_ERR_INVALID_ARG, TAG, "metric key too long");
    ESP_RETURN_ON_FALSE(string_fits(unit, AETUS_METRIC_UNIT_MAX), ESP_ERR_INVALID_ARG, TAG, "metric unit too long");
    ESP_RETURN_ON_FALSE(
        telemetry->metric_count < AETUS_MAX_METRICS,
        ESP_ERR_INVALID_ARG,
        TAG,
        "too many metrics"
    );

    *metric = &telemetry->metrics[telemetry->metric_count];
    memset(*metric, 0, sizeof(**metric));
    copy_string((*metric)->key, sizeof((*metric)->key), key);
    copy_string((*metric)->unit, sizeof((*metric)->unit), unit);
    telemetry->metric_count++;
    return ESP_OK;
}

esp_err_t aetus_telemetry_add_int64(
    aetus_telemetry_t *telemetry,
    const char *key,
    int64_t value,
    const char *unit
)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(append_metric(telemetry, key, unit, &metric), TAG, "append metric failed");
    metric->type = AETUS_METRIC_VALUE_INT64;
    metric->value.int64_value = value;
    return ESP_OK;
}

esp_err_t aetus_telemetry_add_double(
    aetus_telemetry_t *telemetry,
    const char *key,
    double value,
    const char *unit
)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(append_metric(telemetry, key, unit, &metric), TAG, "append metric failed");
    metric->type = AETUS_METRIC_VALUE_DOUBLE;
    metric->value.double_value = value;
    return ESP_OK;
}

esp_err_t aetus_telemetry_add_bool(
    aetus_telemetry_t *telemetry,
    const char *key,
    bool value,
    const char *unit
)
{
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(append_metric(telemetry, key, unit, &metric), TAG, "append metric failed");
    metric->type = AETUS_METRIC_VALUE_BOOL;
    metric->value.bool_value = value;
    return ESP_OK;
}

esp_err_t aetus_telemetry_add_string(
    aetus_telemetry_t *telemetry,
    const char *key,
    const char *value,
    const char *unit
)
{
    ESP_RETURN_ON_FALSE(value != NULL, ESP_ERR_INVALID_ARG, TAG, "string value is required");
    ESP_RETURN_ON_FALSE(
        string_fits(value, AETUS_METRIC_STRING_MAX),
        ESP_ERR_INVALID_ARG,
        TAG,
        "string value too long"
    );
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(append_metric(telemetry, key, unit, &metric), TAG, "append metric failed");
    metric->type = AETUS_METRIC_VALUE_STRING;
    copy_string(metric->value.string_value, sizeof(metric->value.string_value), value);
    return ESP_OK;
}

esp_err_t aetus_telemetry_add_bytes(
    aetus_telemetry_t *telemetry,
    const char *key,
    const uint8_t *value,
    size_t value_size,
    const char *unit
)
{
    ESP_RETURN_ON_FALSE(value != NULL || value_size == 0, ESP_ERR_INVALID_ARG, TAG, "bytes value is required");
    ESP_RETURN_ON_FALSE(value_size <= AETUS_METRIC_BYTES_MAX, ESP_ERR_INVALID_ARG, TAG, "bytes value too large");
    aetus_metric_t *metric = NULL;
    ESP_RETURN_ON_ERROR(append_metric(telemetry, key, unit, &metric), TAG, "append metric failed");
    metric->type = AETUS_METRIC_VALUE_BYTES;
    if (value_size > 0) {
        memcpy(metric->value.bytes_value.data, value, value_size);
    }
    metric->value.bytes_value.size = value_size;
    return ESP_OK;
}

esp_err_t aetus_start(const aetus_config_t *config)
{
    ESP_RETURN_ON_FALSE(config != NULL, ESP_ERR_INVALID_ARG, TAG, "config is required");
    ESP_RETURN_ON_FALSE(
        string_required_fits(config->wifi_ssid, AETUS_WIFI_SSID_MAX),
        ESP_ERR_INVALID_ARG,
        TAG,
        "wifi ssid is required or too long"
    );
    ESP_RETURN_ON_FALSE(
        string_required_fits(config->wifi_password, AETUS_WIFI_PASSWORD_MAX),
        ESP_ERR_INVALID_ARG,
        TAG,
        "wifi password is required or too long"
    );
    ESP_RETURN_ON_FALSE(
        config->wifi_auth == AETUS_WIFI_AUTH_PSK || config->wifi_auth == AETUS_WIFI_AUTH_PEAP,
        ESP_ERR_INVALID_ARG,
        TAG,
        "unsupported wifi auth"
    );
    if (config->wifi_auth == AETUS_WIFI_AUTH_PEAP) {
        ESP_RETURN_ON_FALSE(
            string_required_fits(config->wifi_identity, AETUS_WIFI_IDENTITY_MAX),
            ESP_ERR_INVALID_ARG,
            TAG,
            "wifi peap id is required or too long"
        );
        ESP_RETURN_ON_FALSE(config->wifi_password[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "wifi peap password is empty");
    }
    ESP_RETURN_ON_FALSE(string_required_fits(config->ingest_url, AETUS_URL_MAX), ESP_ERR_INVALID_ARG, TAG, "ingest url is required or too long");
    ESP_RETURN_ON_FALSE(string_fits(config->time_url, AETUS_URL_MAX + 1), ESP_ERR_INVALID_ARG, TAG, "time url too long");
    ESP_RETURN_ON_FALSE(string_required_fits(config->device_id, AETUS_DEVICE_ID_MAX), ESP_ERR_INVALID_ARG, TAG, "device id is required or too long");
    ESP_RETURN_ON_FALSE(string_required_fits(config->device_token, AETUS_DEVICE_TOKEN_MAX), ESP_ERR_INVALID_ARG, TAG, "device token is required or too long");
    ESP_RETURN_ON_FALSE(s_ctx.queue == NULL, ESP_ERR_INVALID_STATE, TAG, "aetus already started");

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.config = *config;
    if (s_ctx.config.upload_interval_ms == 0) {
        s_ctx.config.upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS;
    }
    if (s_ctx.config.queue_depth == 0) {
        s_ctx.config.queue_depth = 16;
    }
    snprintf(s_ctx.boot_id, sizeof(s_ctx.boot_id), "boot-%08lx", (unsigned long)esp_random());

    s_ctx.queue = xQueueCreate(s_ctx.config.queue_depth, sizeof(aetus_queue_item_t));
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_NO_MEM, TAG, "queue allocation failed");
    s_ctx.events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(s_ctx.events != NULL, ESP_ERR_NO_MEM, TAG, "event group allocation failed");
    ESP_RETURN_ON_ERROR(configure_connected_led(&s_ctx), TAG, "connected led setup failed");
    s_ctx.upload_timer = xTimerCreate(
        "aetus_upload_timer",
        pdMS_TO_TICKS(s_ctx.config.upload_interval_ms),
        pdTRUE,
        &s_ctx,
        upload_timer_callback
    );
    ESP_RETURN_ON_FALSE(s_ctx.upload_timer != NULL, ESP_ERR_NO_MEM, TAG, "timer allocation failed");

    BaseType_t task_created = xTaskCreate(
        uploader_task,
        "aetus_upload",
        AETUS_TASK_STACK_BYTES,
        &s_ctx,
        AETUS_TASK_PRIORITY,
        NULL
    );
    ESP_RETURN_ON_FALSE(task_created == pdPASS, ESP_ERR_NO_MEM, TAG, "task allocation failed");
    ESP_RETURN_ON_FALSE(xTimerStart(s_ctx.upload_timer, 0) == pdPASS, ESP_FAIL, TAG, "timer start failed");

    ESP_LOGI(
        TAG,
        "started device_id=%s boot_id=%s interval_ms=%u queue_depth=%u",
        s_ctx.config.device_id,
        s_ctx.boot_id,
        (unsigned)s_ctx.config.upload_interval_ms,
        (unsigned)s_ctx.config.queue_depth
    );
    return ESP_OK;
}

esp_err_t aetus_update_config(const aetus_config_t *config)
{
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    ESP_RETURN_ON_FALSE(config != NULL, ESP_ERR_INVALID_ARG, TAG, "config is required");

    aetus_config_t next = *config;
    if (next.upload_interval_ms == 0) {
        next.upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS;
    }
    if (next.queue_depth == 0) {
        next.queue_depth = s_ctx.config.queue_depth;
    }
    ESP_RETURN_ON_FALSE(
        string_required_fits(next.wifi_ssid, AETUS_WIFI_SSID_MAX),
        ESP_ERR_INVALID_ARG,
        TAG,
        "wifi ssid is required or too long"
    );
    ESP_RETURN_ON_FALSE(
        string_required_fits(next.wifi_password, AETUS_WIFI_PASSWORD_MAX),
        ESP_ERR_INVALID_ARG,
        TAG,
        "wifi password is required or too long"
    );
    ESP_RETURN_ON_FALSE(
        next.wifi_auth == AETUS_WIFI_AUTH_PSK || next.wifi_auth == AETUS_WIFI_AUTH_PEAP,
        ESP_ERR_INVALID_ARG,
        TAG,
        "unsupported wifi auth"
    );
    if (next.wifi_auth == AETUS_WIFI_AUTH_PEAP) {
        ESP_RETURN_ON_FALSE(
            string_required_fits(next.wifi_identity, AETUS_WIFI_IDENTITY_MAX),
            ESP_ERR_INVALID_ARG,
            TAG,
            "wifi peap id is required or too long"
        );
    }
    ESP_RETURN_ON_FALSE(string_required_fits(next.ingest_url, AETUS_URL_MAX), ESP_ERR_INVALID_ARG, TAG, "ingest url is required or too long");
    ESP_RETURN_ON_FALSE(string_fits(next.time_url, AETUS_URL_MAX + 1), ESP_ERR_INVALID_ARG, TAG, "time url too long");
    ESP_RETURN_ON_FALSE(string_required_fits(next.device_id, AETUS_DEVICE_ID_MAX), ESP_ERR_INVALID_ARG, TAG, "device id is required or too long");
    ESP_RETURN_ON_FALSE(string_required_fits(next.device_token, AETUS_DEVICE_TOKEN_MAX), ESP_ERR_INVALID_ARG, TAG, "device token is required or too long");

    s_ctx.config = next;
    ESP_RETURN_ON_ERROR(configure_connected_led(&s_ctx), TAG, "connected led setup failed");
    if (s_ctx.upload_timer != NULL) {
        xTimerChangePeriod(s_ctx.upload_timer, pdMS_TO_TICKS(s_ctx.config.upload_interval_ms), 0);
    }
    if (s_ctx.wifi_started) {
        xEventGroupClearBits(s_ctx.events, AETUS_WIFI_CONNECTED_BIT);
        if (s_ctx.config.connected_led_enabled) {
            gpio_set_level((gpio_num_t)s_ctx.config.connected_led_gpio, 0);
        }
        ESP_RETURN_ON_ERROR(wifi_apply_sta_config(&s_ctx), TAG, "wifi config update failed");
        esp_wifi_disconnect();
        esp_wifi_connect();
    }
    return ESP_OK;
}

esp_err_t aetus_get_config(aetus_config_t *config)
{
    ESP_RETURN_ON_FALSE(config != NULL, ESP_ERR_INVALID_ARG, TAG, "config output is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    *config = s_ctx.config;
    return ESP_OK;
}

esp_err_t aetus_sync_rtc(TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    ESP_RETURN_ON_ERROR(wifi_wait_connected_for(&s_ctx, timeout), TAG, "wifi connect before time sync failed");

    uint64_t unix_time_ns = 0;
    ESP_RETURN_ON_ERROR(fetch_server_time_ns(&s_ctx, &unix_time_ns), TAG, "server time fetch failed");
    ESP_RETURN_ON_ERROR(set_rtc_from_unix_time_ns(unix_time_ns), TAG, "rtc set failed");

    ESP_LOGI(TAG, "AETUS_RTC_SYNC_OK unix_time_ns=%llu", unix_time_ns);
    return ESP_OK;
}

esp_err_t aetus_enqueue_telemetry(const aetus_telemetry_t *telemetry, TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(telemetry != NULL, ESP_ERR_INVALID_ARG, TAG, "telemetry is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");

    aetus_queue_item_t item = {
        .kind = AETUS_QUEUE_ITEM_TELEMETRY,
        .body.telemetry = *telemetry,
    };
    return xQueueSend(s_ctx.queue, &item, timeout) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t aetus_enqueue_status(const aetus_status_t *status, TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(status != NULL, ESP_ERR_INVALID_ARG, TAG, "status is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");

    aetus_queue_item_t item = {
        .kind = AETUS_QUEUE_ITEM_STATUS,
        .body.status = *status,
    };
    return xQueueSend(s_ctx.queue, &item, timeout) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t aetus_flush(TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(s_ctx.events != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    xEventGroupClearBits(s_ctx.events, AETUS_UPLOAD_DONE_BIT);
    xEventGroupSetBits(s_ctx.events, AETUS_UPLOAD_FLUSH_BIT);
    EventBits_t bits = xEventGroupWaitBits(
        s_ctx.events,
        AETUS_UPLOAD_DONE_BIT,
        pdTRUE,
        pdTRUE,
        timeout
    );
    return (bits & AETUS_UPLOAD_DONE_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}
