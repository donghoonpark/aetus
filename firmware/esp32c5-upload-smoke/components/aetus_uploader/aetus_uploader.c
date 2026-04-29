#include "aetus_uploader.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
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
#define AETUS_HTTP_TIMEOUT_MS 10000
#define AETUS_TASK_STACK_BYTES 8192
#define AETUS_TASK_PRIORITY 5
#define AETUS_WIFI_CONNECT_TIMEOUT_MS 15000

static const char *TAG = "aetus_uploader";

typedef struct {
    aetus_uploader_config_t config;
    QueueHandle_t queue;
    EventGroupHandle_t events;
    TimerHandle_t upload_timer;
    uint64_t sequence;
    char boot_id[32];
    bool wifi_started;
} aetus_uploader_ctx_t;

static aetus_uploader_ctx_t s_ctx;

static void copy_string(char *target, size_t target_size, const char *source)
{
    if (target_size == 0) {
        return;
    }

    strncpy(target, source, target_size - 1);
    target[target_size - 1] = '\0';
}

static void wifi_event_handler(
    void *arg,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
)
{
    aetus_uploader_ctx_t *ctx = (aetus_uploader_ctx_t *)arg;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(ctx->events, AETUS_WIFI_CONNECTED_BIT);
        ESP_LOGW(TAG, "wifi disconnected, reconnecting");
        esp_wifi_connect();
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "wifi got ip " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(ctx->events, AETUS_WIFI_CONNECTED_BIT);
    }
}

static esp_err_t wifi_start(aetus_uploader_ctx_t *ctx)
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
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif init failed");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop init failed");
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

    wifi_config_t wifi_config = {0};
    copy_string((char *)wifi_config.sta.ssid, sizeof(wifi_config.sta.ssid), ctx->config.wifi_ssid);
    copy_string((char *)wifi_config.sta.password, sizeof(wifi_config.sta.password), ctx->config.wifi_password);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "wifi mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config), TAG, "wifi config failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start failed");
    ctx->wifi_started = true;
    return ESP_OK;
}

static esp_err_t wifi_wait_connected(aetus_uploader_ctx_t *ctx)
{
    ESP_RETURN_ON_ERROR(wifi_start(ctx), TAG, "wifi start failed");
    EventBits_t bits = xEventGroupWaitBits(
        ctx->events,
        AETUS_WIFI_CONNECTED_BIT,
        pdFALSE,
        pdTRUE,
        pdMS_TO_TICKS(AETUS_WIFI_CONNECT_TIMEOUT_MS)
    );
    return (bits & AETUS_WIFI_CONNECTED_BIT) ? ESP_OK : ESP_ERR_TIMEOUT;
}

static bool encode_message(
    aetus_uploader_ctx_t *ctx,
    const aetus_upload_message_t *message,
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size
)
{
    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    event.schema_version = 1;
    copy_string(event.device_id, sizeof(event.device_id), ctx->config.device_id);
    event.sequence = ctx->sequence;
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    copy_string(event.boot_id, sizeof(event.boot_id), ctx->boot_id);
    event.firmware_version = ctx->config.firmware_version;
    event.uptime_ms = (uint64_t)(esp_timer_get_time() / 1000);
    event.timestamp_ns = message->timestamp_ns;
    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;

    uint32_t metric_count = message->metric_count;
    if (metric_count > AETUS_MAX_METRICS) {
        metric_count = AETUS_MAX_METRICS;
    }
    event.body.telemetry.metrics_count = metric_count;

    for (uint32_t index = 0; index < metric_count; index++) {
        aetus_ingest_v1_Metric *metric = &event.body.telemetry.metrics[index];
        copy_string(metric->key, sizeof(metric->key), message->metrics[index].key);
        metric->which_value = aetus_ingest_v1_Metric_double_value_tag;
        metric->value.double_value = message->metrics[index].value;
        copy_string(metric->unit, sizeof(metric->unit), message->metrics[index].unit);
    }

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        ESP_LOGE(TAG, "protobuf encode failed");
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

static esp_err_t post_payload(aetus_uploader_ctx_t *ctx, const uint8_t *payload, size_t payload_size)
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

static void drain_queue(aetus_uploader_ctx_t *ctx)
{
    esp_err_t err = wifi_wait_connected(ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "wifi not connected before upload: %s", esp_err_to_name(err));
        return;
    }

    uint32_t uploaded = 0;
    aetus_upload_message_t message;
    while (xQueueReceive(ctx->queue, &message, 0) == pdTRUE) {
        uint8_t payload[512];
        size_t payload_size = 0;
        if (!encode_message(ctx, &message, payload, sizeof(payload), &payload_size)) {
            continue;
        }

        err = post_payload(ctx, payload, payload_size);
        if (err == ESP_OK) {
            ctx->sequence++;
            uploaded++;
        } else {
            ESP_LOGW(TAG, "AETUS_UPLOAD_FAILED sequence=%llu", ctx->sequence);
            break;
        }
    }

    if (uploaded == 0) {
        ESP_LOGI(TAG, "AETUS_UPLOAD_EMPTY");
    }
}

static void upload_timer_callback(TimerHandle_t timer)
{
    aetus_uploader_ctx_t *ctx = (aetus_uploader_ctx_t *)pvTimerGetTimerID(timer);
    xEventGroupSetBits(ctx->events, AETUS_UPLOAD_DUE_BIT);
}

static void uploader_task(void *arg)
{
    aetus_uploader_ctx_t *ctx = (aetus_uploader_ctx_t *)arg;
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

esp_err_t aetus_uploader_start(const aetus_uploader_config_t *config)
{
    ESP_RETURN_ON_FALSE(config != NULL, ESP_ERR_INVALID_ARG, TAG, "config is required");
    ESP_RETURN_ON_FALSE(config->wifi_ssid != NULL, ESP_ERR_INVALID_ARG, TAG, "wifi ssid is required");
    ESP_RETURN_ON_FALSE(config->wifi_password != NULL, ESP_ERR_INVALID_ARG, TAG, "wifi password is required");
    ESP_RETURN_ON_FALSE(config->ingest_url != NULL, ESP_ERR_INVALID_ARG, TAG, "ingest url is required");
    ESP_RETURN_ON_FALSE(config->device_id != NULL, ESP_ERR_INVALID_ARG, TAG, "device id is required");
    ESP_RETURN_ON_FALSE(config->device_token != NULL, ESP_ERR_INVALID_ARG, TAG, "device token is required");

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.config = *config;
    if (s_ctx.config.upload_interval_ms == 0) {
        s_ctx.config.upload_interval_ms = AETUS_UPLOAD_DEFAULT_INTERVAL_MS;
    }
    if (s_ctx.config.queue_depth == 0) {
        s_ctx.config.queue_depth = 16;
    }
    snprintf(s_ctx.boot_id, sizeof(s_ctx.boot_id), "boot-%08lx", (unsigned long)esp_random());

    s_ctx.queue = xQueueCreate(s_ctx.config.queue_depth, sizeof(aetus_upload_message_t));
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_NO_MEM, TAG, "queue allocation failed");
    s_ctx.events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(s_ctx.events != NULL, ESP_ERR_NO_MEM, TAG, "event group allocation failed");
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
        "aetus_uploader",
        AETUS_TASK_STACK_BYTES,
        &s_ctx,
        AETUS_TASK_PRIORITY,
        NULL
    );
    ESP_RETURN_ON_FALSE(task_created == pdPASS, ESP_ERR_NO_MEM, TAG, "task allocation failed");
    ESP_RETURN_ON_FALSE(xTimerStart(s_ctx.upload_timer, 0) == pdPASS, ESP_FAIL, TAG, "timer start failed");

    ESP_LOGI(
        TAG,
        "uploader started device_id=%s boot_id=%s interval_ms=%u",
        s_ctx.config.device_id,
        s_ctx.boot_id,
        (unsigned)s_ctx.config.upload_interval_ms
    );
    return ESP_OK;
}

esp_err_t aetus_uploader_enqueue(const aetus_upload_message_t *message, TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(message != NULL, ESP_ERR_INVALID_ARG, TAG, "message is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "uploader not started");
    return xQueueSend(s_ctx.queue, message, timeout) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t aetus_uploader_flush(TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(s_ctx.events != NULL, ESP_ERR_INVALID_STATE, TAG, "uploader not started");
    xEventGroupSetBits(s_ctx.events, AETUS_UPLOAD_FLUSH_BIT);
    vTaskDelay(timeout);
    return ESP_OK;
}
