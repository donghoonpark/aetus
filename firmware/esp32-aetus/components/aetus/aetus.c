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
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "mbedtls/md.h"
#include "nvs_flash.h"
#include "pb_encode.h"

#include "aetus_signal_sample_pool.h"
#include "ingest.pb.h"

#define AETUS_WIFI_CONNECTED_BIT BIT0
#define AETUS_UPLOAD_DUE_BIT BIT1
#define AETUS_UPLOAD_FLUSH_BIT BIT2
#define AETUS_UPLOAD_DONE_BIT BIT3
#define AETUS_HTTP_TIMEOUT_MS 10000
#ifndef AETUS_TASK_STACK_BYTES
#ifdef CONFIG_AETUS_TASK_STACK_BYTES
#define AETUS_TASK_STACK_BYTES CONFIG_AETUS_TASK_STACK_BYTES
#else
#define AETUS_TASK_STACK_BYTES 12288
#endif
#endif
#define AETUS_TASK_PRIORITY 5
#define AETUS_WIFI_CONNECT_TIMEOUT_MS 15000
#ifndef AETUS_ENCODE_BUFFER_BYTES
#ifdef CONFIG_AETUS_ENCODE_BUFFER_BYTES
#define AETUS_ENCODE_BUFFER_BYTES CONFIG_AETUS_ENCODE_BUFFER_BYTES
#else
#define AETUS_ENCODE_BUFFER_BYTES 4096
#endif
#endif
#ifndef AETUS_SIGNAL_FRAME_ENCODE_OVERHEAD_BYTES
#define AETUS_SIGNAL_FRAME_ENCODE_OVERHEAD_BYTES 512
#endif
#ifndef AETUS_QUEUE_ITEM_MAX_BYTES
#ifdef CONFIG_AETUS_QUEUE_ITEM_MAX_BYTES
#define AETUS_QUEUE_ITEM_MAX_BYTES CONFIG_AETUS_QUEUE_ITEM_MAX_BYTES
#else
#define AETUS_QUEUE_ITEM_MAX_BYTES 4096
#endif
#endif
#define AETUS_TIME_RESPONSE_BUFFER_BYTES 512
#define AETUS_INGEST_PATH "/v1/ingest"
#define AETUS_TIME_PATH "/v1/time"
#define AETUS_HMAC_SCHEME "hmac-sha256-v1"
#define AETUS_HMAC_PREFIX "AETUS-HMAC-SHA256-V1\nPOST\n/v1/ingest\n"

AETUS_STATIC_ASSERT(
    AETUS_ENCODE_BUFFER_BYTES >= (AETUS_SIGNAL_SAMPLES_MAX + AETUS_SIGNAL_FRAME_ENCODE_OVERHEAD_BYTES),
    "AETUS_ENCODE_BUFFER_BYTES must cover max signal samples plus protobuf envelope overhead"
);

static const char *TAG = "aetus";

typedef enum {
    AETUS_QUEUE_ITEM_TELEMETRY = 0,
    AETUS_QUEUE_ITEM_STATUS = 1,
    AETUS_QUEUE_ITEM_SIGNAL_FRAME = 2,
} aetus_queue_item_kind_t;

typedef struct {
    aetus_queue_item_kind_t kind;
    void *signal_sample_owner;
    union {
        aetus_telemetry_t telemetry;
        aetus_status_t status;
        aetus_signal_frame_t signal_frame;
    } body;
} aetus_queue_item_t;

AETUS_STATIC_ASSERT(
    sizeof(aetus_queue_item_t) <= AETUS_QUEUE_ITEM_MAX_BYTES,
    "aetus_queue_item_t exceeds the configured FreeRTOS queue slot budget"
);

typedef struct {
    const uint8_t *data;
    size_t size;
} aetus_bytes_arg_t;

typedef struct {
    const aetus_signal_frame_t *frame;
} aetus_signal_frame_arg_t;

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
    bool wifi_reconfiguring;
    aetus_signal_sample_pool_stats_t signal_pool_stats;
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

static void release_queue_item(aetus_ctx_t *ctx, aetus_queue_item_t *item)
{
    if (item == NULL || item->kind != AETUS_QUEUE_ITEM_SIGNAL_FRAME) {
        return;
    }
    aetus_signal_sample_pool_release(
        ctx->config.signal_sample_pool_backend,
        &ctx->signal_pool_stats,
        item->signal_sample_owner
    );
    item->signal_sample_owner = NULL;
    item->body.signal_frame.samples = NULL;
    item->body.signal_frame.samples_size = 0U;
}

static void bytes_to_hex(const uint8_t *bytes, size_t byte_count, char *hex, size_t hex_size)
{
    static const char digits[] = "0123456789abcdef";
    if (hex_size < (byte_count * 2U) + 1U) {
        if (hex_size > 0) {
            hex[0] = '\0';
        }
        return;
    }
    for (size_t index = 0; index < byte_count; index++) {
        hex[index * 2U] = digits[bytes[index] >> 4U];
        hex[(index * 2U) + 1U] = digits[bytes[index] & 0x0fU];
    }
    hex[byte_count * 2U] = '\0';
}

static esp_err_t sha256_digest_parts(
    const uint8_t *part0,
    size_t part0_size,
    const uint8_t *part1,
    size_t part1_size,
    const uint8_t *part2,
    size_t part2_size,
    uint8_t digest[32]
)
{
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    ESP_RETURN_ON_FALSE(info != NULL, ESP_FAIL, TAG, "sha256 md info unavailable");

    mbedtls_md_context_t ctx;
    mbedtls_md_init(&ctx);
    int rc = mbedtls_md_setup(&ctx, info, 0);
    if (rc == 0) {
        rc = mbedtls_md_starts(&ctx);
    }
    if (rc == 0 && part0 != NULL && part0_size > 0) {
        rc = mbedtls_md_update(&ctx, part0, part0_size);
    }
    if (rc == 0 && part1 != NULL && part1_size > 0) {
        rc = mbedtls_md_update(&ctx, part1, part1_size);
    }
    if (rc == 0 && part2 != NULL && part2_size > 0) {
        rc = mbedtls_md_update(&ctx, part2, part2_size);
    }
    if (rc == 0) {
        rc = mbedtls_md_finish(&ctx, digest);
    }
    mbedtls_md_free(&ctx);
    ESP_RETURN_ON_FALSE(rc == 0, ESP_FAIL, TAG, "sha256 failed rc=%d", rc);

    return ESP_OK;
}

static esp_err_t sha256_hex(const uint8_t *payload, size_t payload_size, char *hex, size_t hex_size)
{
    uint8_t digest[32];
    ESP_RETURN_ON_ERROR(
        sha256_digest_parts(payload, payload_size, NULL, 0, NULL, 0, digest),
        TAG,
        "sha256 digest failed"
    );
    bytes_to_hex(digest, sizeof(digest), hex, hex_size);
    return ESP_OK;
}

static esp_err_t hmac_sha256_digest(
    const uint8_t *key,
    size_t key_size,
    const uint8_t *message0,
    size_t message0_size,
    const uint8_t *message1,
    size_t message1_size,
    uint8_t digest[32]
)
{
    uint8_t key_block[64] = {0};
    if (key_size > sizeof(key_block)) {
        ESP_RETURN_ON_ERROR(
            sha256_digest_parts(key, key_size, NULL, 0, NULL, 0, key_block),
            TAG,
            "hmac key hash failed"
        );
    } else {
        memcpy(key_block, key, key_size);
    }

    uint8_t ipad[64];
    uint8_t opad[64];
    for (size_t index = 0; index < sizeof(key_block); index++) {
        ipad[index] = key_block[index] ^ 0x36U;
        opad[index] = key_block[index] ^ 0x5cU;
    }

    uint8_t inner_digest[32];
    ESP_RETURN_ON_ERROR(
        sha256_digest_parts(ipad, sizeof(ipad), message0, message0_size, message1, message1_size, inner_digest),
        TAG,
        "hmac inner hash failed"
    );
    ESP_RETURN_ON_ERROR(
        sha256_digest_parts(opad, sizeof(opad), inner_digest, sizeof(inner_digest), NULL, 0, digest),
        TAG,
        "hmac outer hash failed"
    );
    return ESP_OK;
}

static esp_err_t hmac_signature_header(
    const aetus_ctx_t *ctx,
    const uint8_t *payload,
    size_t payload_size,
    char *header,
    size_t header_size
)
{
    char body_sha256_hex[65];
    uint8_t digest[32];
    ESP_RETURN_ON_ERROR(sha256_hex(payload, payload_size, body_sha256_hex, sizeof(body_sha256_hex)), TAG, "body sha256 failed");

    char signing_prefix[128];
    int prefix_len = snprintf(
        signing_prefix,
        sizeof(signing_prefix),
        "%s%s\n",
        AETUS_HMAC_PREFIX,
        ctx->config.device_id
    );
    ESP_RETURN_ON_FALSE(
        prefix_len > 0 && (size_t)prefix_len < sizeof(signing_prefix),
        ESP_ERR_INVALID_SIZE,
        TAG,
        "hmac signing prefix too long"
    );

    ESP_RETURN_ON_ERROR(
        hmac_sha256_digest(
            (const uint8_t *)ctx->config.device_token,
            strlen(ctx->config.device_token),
            (const uint8_t *)signing_prefix,
            (size_t)prefix_len,
            (const uint8_t *)body_sha256_hex,
            strlen(body_sha256_hex),
            digest
        ),
        TAG,
        "hmac digest failed"
    );

    char signature_hex[65];
    bytes_to_hex(digest, sizeof(digest), signature_hex, sizeof(signature_hex));
    int written = snprintf(header, header_size, "%s=%s", AETUS_HMAC_SCHEME, signature_hex);
    ESP_RETURN_ON_FALSE(
        written > 0 && (size_t)written < header_size,
        ESP_ERR_INVALID_SIZE,
        TAG,
        "hmac signature header too long"
    );
    return ESP_OK;
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

static bool encode_signal_channels_callback(pb_ostream_t *stream, const pb_field_t *field, void *const *arg)
{
    const aetus_signal_frame_arg_t *value = (const aetus_signal_frame_arg_t *)(*arg);
    if (value == NULL || value->frame == NULL) {
        return true;
    }

    uint32_t channel_count = value->frame->channel_count;
    if (channel_count > AETUS_SIGNAL_CHANNELS_MAX) {
        channel_count = AETUS_SIGNAL_CHANNELS_MAX;
    }

    for (uint32_t index = 0; index < channel_count; index++) {
        const aetus_signal_channel_t *source = &value->frame->channels[index];
        aetus_ingest_v1_SignalChannel channel = aetus_ingest_v1_SignalChannel_init_zero;

        copy_string(channel.key, sizeof(channel.key), source->key);
        copy_string(channel.unit, sizeof(channel.unit), source->unit);
        channel.has_scale = source->has_scale;
        channel.scale = source->scale;
        channel.has_offset = source->has_offset;
        channel.offset = source->offset;

        if (!pb_encode_tag_for_field(stream, field)) {
            return false;
        }
        if (!pb_encode_submessage(stream, aetus_ingest_v1_SignalChannel_fields, &channel)) {
            return false;
        }
    }

    return true;
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

static aetus_ingest_v1_SignalSampleEncoding map_signal_encoding(aetus_signal_encoding_t encoding)
{
    switch (encoding) {
    case AETUS_SIGNAL_ENCODING_INT16_LE:
        return aetus_ingest_v1_SignalSampleEncoding_SIGNAL_SAMPLE_ENCODING_INT16_LE;
    case AETUS_SIGNAL_ENCODING_UINT16_LE:
        return aetus_ingest_v1_SignalSampleEncoding_SIGNAL_SAMPLE_ENCODING_UINT16_LE;
    case AETUS_SIGNAL_ENCODING_INT32_LE:
        return aetus_ingest_v1_SignalSampleEncoding_SIGNAL_SAMPLE_ENCODING_INT32_LE;
    case AETUS_SIGNAL_ENCODING_FLOAT32_LE:
    default:
        return aetus_ingest_v1_SignalSampleEncoding_SIGNAL_SAMPLE_ENCODING_FLOAT32_LE;
    }
}

static aetus_ingest_v1_SignalSampleLayout map_signal_layout(aetus_signal_layout_t layout)
{
    switch (layout) {
    case AETUS_SIGNAL_LAYOUT_PLANAR:
        return aetus_ingest_v1_SignalSampleLayout_SIGNAL_SAMPLE_LAYOUT_PLANAR;
    case AETUS_SIGNAL_LAYOUT_INTERLEAVED:
    default:
        return aetus_ingest_v1_SignalSampleLayout_SIGNAL_SAMPLE_LAYOUT_INTERLEAVED;
    }
}

static size_t signal_sample_width_bytes(aetus_signal_encoding_t encoding)
{
    switch (encoding) {
    case AETUS_SIGNAL_ENCODING_INT16_LE:
    case AETUS_SIGNAL_ENCODING_UINT16_LE:
        return 2U;
    case AETUS_SIGNAL_ENCODING_FLOAT32_LE:
    case AETUS_SIGNAL_ENCODING_INT32_LE:
        return 4U;
    default:
        return 0U;
    }
}

static esp_err_t expected_signal_samples_size(const aetus_signal_frame_t *frame, size_t *expected_size)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(expected_size != NULL, ESP_ERR_INVALID_ARG, TAG, "expected size output is required");

    size_t sample_width = signal_sample_width_bytes(frame->encoding);
    ESP_RETURN_ON_FALSE(sample_width > 0U, ESP_ERR_INVALID_ARG, TAG, "signal encoding unsupported");
    ESP_RETURN_ON_FALSE(
        frame->channel_count == 0U || (size_t)frame->sample_count <= (SIZE_MAX / (size_t)frame->channel_count),
        ESP_ERR_INVALID_ARG,
        TAG,
        "signal frame size overflow"
    );

    size_t total = (size_t)frame->sample_count * (size_t)frame->channel_count;
    ESP_RETURN_ON_FALSE(total == 0U || total <= (SIZE_MAX / sample_width), ESP_ERR_INVALID_ARG, TAG, "signal frame byte size overflow");
    *expected_size = total * sample_width;
    return ESP_OK;
}

static esp_err_t validate_signal_frame(const aetus_signal_frame_t *frame)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(frame->stream_key[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "signal frame stream key is required");
    ESP_RETURN_ON_FALSE(frame->sample_interval_ns > 0U, ESP_ERR_INVALID_ARG, TAG, "signal frame sample interval is required");
    ESP_RETURN_ON_FALSE(frame->sample_count > 0U, ESP_ERR_INVALID_ARG, TAG, "signal frame sample count is required");
    ESP_RETURN_ON_FALSE(frame->channel_count > 0U, ESP_ERR_INVALID_ARG, TAG, "signal frame channel count is required");
    ESP_RETURN_ON_FALSE(frame->channel_count <= AETUS_SIGNAL_CHANNELS_MAX, ESP_ERR_INVALID_ARG, TAG, "too many signal channels");
    ESP_RETURN_ON_FALSE(frame->samples_size > 0U, ESP_ERR_INVALID_ARG, TAG, "signal frame samples are required");
    ESP_RETURN_ON_FALSE(frame->samples != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame sample pointer is required");
    ESP_RETURN_ON_FALSE(frame->samples_size <= AETUS_SIGNAL_SAMPLES_MAX, ESP_ERR_INVALID_ARG, TAG, "signal frame samples too large");

    for (uint32_t index = 0; index < frame->channel_count; index++) {
        ESP_RETURN_ON_FALSE(frame->channels[index].key[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "signal channel key is required");
    }

    size_t expected_size = 0;
    ESP_RETURN_ON_ERROR(expected_signal_samples_size(frame, &expected_size), TAG, "signal frame size validation failed");
    ESP_RETURN_ON_FALSE(expected_size == frame->samples_size, ESP_ERR_INVALID_ARG, TAG, "signal frame sample size mismatch");
    return ESP_OK;
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
        if (ctx->wifi_reconfiguring) {
            ESP_LOGI(TAG, "wifi disconnected during config update; reconnect deferred");
            return;
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
    ESP_LOGI(
        TAG,
        "wifi config applied ssid=%s auth=%s password_len=%u",
        ctx->config.wifi_ssid,
        ctx->config.wifi_auth == AETUS_WIFI_AUTH_PEAP ? "peap" : "psk",
        (unsigned)strlen(ctx->config.wifi_password)
    );
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
    event.body.telemetry.which_payload = aetus_ingest_v1_TelemetryPayload_metric_set_tag;
    event.body.telemetry.payload.metric_set.metrics_count = metric_count;

    for (uint32_t index = 0; index < metric_count; index++) {
        fill_metric(
            &event.body.telemetry.payload.metric_set.metrics[index],
            &telemetry->metrics[index],
            &bytes_args[index]
        );
    }

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        ESP_LOGE(TAG, "protobuf telemetry encode failed");
        return false;
    }

    *encoded_size = stream.bytes_written;
    return true;
}

static bool encode_signal_frame(
    aetus_ctx_t *ctx,
    const aetus_signal_frame_t *frame,
    uint8_t *buffer,
    size_t buffer_size,
    size_t *encoded_size
)
{
    if (validate_signal_frame(frame) != ESP_OK) {
        return false;
    }

    aetus_ingest_v1_IngestEvent event = aetus_ingest_v1_IngestEvent_init_zero;
    aetus_signal_frame_arg_t channel_arg = {
        .frame = frame,
    };
    aetus_bytes_arg_t samples_arg = {
        .data = frame->samples,
        .size = frame->samples_size,
    };

    fill_event_header(ctx, &event);
    event.event_type = aetus_ingest_v1_EventType_EVENT_TYPE_TELEMETRY;
    event.timestamp_ns = frame->timestamp_ns;
    event.which_body = aetus_ingest_v1_IngestEvent_telemetry_tag;
    event.body.telemetry.which_payload = aetus_ingest_v1_TelemetryPayload_signal_frame_tag;

    aetus_ingest_v1_SignalFrame *target = &event.body.telemetry.payload.signal_frame;
    copy_string(target->stream_key, sizeof(target->stream_key), frame->stream_key);
    target->sample_interval_ns = frame->sample_interval_ns;
    target->sample_count = frame->sample_count;
    target->encoding = map_signal_encoding(frame->encoding);
    target->layout = map_signal_layout(frame->layout);
    target->channels.funcs.encode = encode_signal_channels_callback;
    target->channels.arg = &channel_arg;
    target->samples.funcs.encode = encode_bytes_callback;
    target->samples.arg = &samples_arg;

    pb_ostream_t stream = pb_ostream_from_buffer(buffer, buffer_size);
    if (!pb_encode(&stream, aetus_ingest_v1_IngestEvent_fields, &event)) {
        ESP_LOGE(TAG, "protobuf signal frame encode failed");
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
    if (item->kind == AETUS_QUEUE_ITEM_SIGNAL_FRAME) {
        return encode_signal_frame(ctx, &item->body.signal_frame, buffer, buffer_size, encoded_size);
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

    esp_http_client_set_header(client, "Content-Type", "application/x-protobuf");
    esp_http_client_set_header(client, "X-Device-Id", ctx->config.device_id);

    if (ctx->config.auth_mode == AETUS_AUTH_HMAC_SHA256) {
        char signature_header[96];
        esp_err_t sign_err = hmac_signature_header(ctx, payload, payload_size, signature_header, sizeof(signature_header));
        if (sign_err != ESP_OK) {
            esp_http_client_cleanup(client);
            return sign_err;
        }
        esp_http_client_set_header(client, "X-Aetus-Signature", signature_header);
    } else {
        char authorization[128];
        snprintf(authorization, sizeof(authorization), "Bearer %s", ctx->config.device_token);
        esp_http_client_set_header(client, "Authorization", authorization);
    }
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
            if (item.kind == AETUS_QUEUE_ITEM_SIGNAL_FRAME) {
                aetus_signal_sample_pool_note_validation_failure(&ctx->signal_pool_stats);
            }
            release_queue_item(ctx, &item);
            continue;
        }

        err = post_payload(ctx, payload, payload_size);
        if (err == ESP_OK) {
            ctx->sequence++;
            uploaded++;
            if (item.kind == AETUS_QUEUE_ITEM_SIGNAL_FRAME) {
                aetus_signal_sample_pool_note_upload_success(&ctx->signal_pool_stats);
            }
            release_queue_item(ctx, &item);
            continue;
        }

        ESP_LOGW(TAG, "AETUS_UPLOAD_FAILED sequence=%llu; requeueing", ctx->sequence);
        if (xQueueSendToFront(ctx->queue, &item, 0) != pdTRUE) {
            ESP_LOGE(TAG, "failed message dropped because queue is full");
            if (item.kind == AETUS_QUEUE_ITEM_SIGNAL_FRAME) {
                aetus_signal_sample_pool_note_final_drop(&ctx->signal_pool_stats);
            }
            release_queue_item(ctx, &item);
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

void aetus_signal_frame_init(aetus_signal_frame_t *frame)
{
    if (frame == NULL) {
        return;
    }
    memset(frame, 0, sizeof(*frame));
    frame->encoding = AETUS_SIGNAL_ENCODING_FLOAT32_LE;
    frame->layout = AETUS_SIGNAL_LAYOUT_INTERLEAVED;
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

esp_err_t aetus_signal_frame_set_timestamp_rtc(aetus_signal_frame_t *frame)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    return aetus_rtc_timestamp_ns(&frame->timestamp_ns);
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

esp_err_t aetus_signal_frame_set_stream_key(aetus_signal_frame_t *frame, const char *stream_key)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(stream_key != NULL && stream_key[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "signal stream key is required");
    ESP_RETURN_ON_FALSE(string_fits(stream_key, sizeof(frame->stream_key)), ESP_ERR_INVALID_ARG, TAG, "signal stream key too long");
    copy_string(frame->stream_key, sizeof(frame->stream_key), stream_key);
    return ESP_OK;
}

esp_err_t aetus_signal_frame_add_channel(
    aetus_signal_frame_t *frame,
    const char *key,
    const char *unit,
    const float *scale,
    const float *offset
)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(key != NULL && key[0] != '\0', ESP_ERR_INVALID_ARG, TAG, "signal channel key is required");
    ESP_RETURN_ON_FALSE(string_fits(key, AETUS_METRIC_KEY_MAX), ESP_ERR_INVALID_ARG, TAG, "signal channel key too long");
    ESP_RETURN_ON_FALSE(string_fits(unit, AETUS_METRIC_UNIT_MAX), ESP_ERR_INVALID_ARG, TAG, "signal channel unit too long");
    ESP_RETURN_ON_FALSE(frame->channel_count < AETUS_SIGNAL_CHANNELS_MAX, ESP_ERR_INVALID_ARG, TAG, "too many signal channels");

    aetus_signal_channel_t *channel = &frame->channels[frame->channel_count];
    memset(channel, 0, sizeof(*channel));
    copy_string(channel->key, sizeof(channel->key), key);
    copy_string(channel->unit, sizeof(channel->unit), unit);
    if (scale != NULL) {
        channel->has_scale = true;
        channel->scale = *scale;
    }
    if (offset != NULL) {
        channel->has_offset = true;
        channel->offset = *offset;
    }

    frame->channel_count++;
    return ESP_OK;
}

esp_err_t aetus_signal_frame_set_samples(aetus_signal_frame_t *frame, const void *samples, size_t samples_size)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(samples != NULL || samples_size == 0U, ESP_ERR_INVALID_ARG, TAG, "signal frame samples are required");
    ESP_RETURN_ON_FALSE(samples_size <= AETUS_SIGNAL_SAMPLES_MAX, ESP_ERR_INVALID_ARG, TAG, "signal frame samples too large");

    frame->samples = (const uint8_t *)samples;
    frame->samples_size = samples_size;
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
    ESP_RETURN_ON_FALSE(
        config->auth_mode == AETUS_AUTH_BEARER || config->auth_mode == AETUS_AUTH_HMAC_SHA256,
        ESP_ERR_INVALID_ARG,
        TAG,
        "unsupported auth mode"
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
    if (s_ctx.config.signal_sample_pool_backend != AETUS_SIGNAL_SAMPLE_POOL_STATIC &&
        s_ctx.config.signal_sample_pool_backend != AETUS_SIGNAL_SAMPLE_POOL_FREERTOS_HEAP) {
        s_ctx.config.signal_sample_pool_backend = AETUS_SIGNAL_SAMPLE_POOL_STATIC;
    }
    aetus_signal_sample_pool_reset(&s_ctx.signal_pool_stats);
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
    if (next.signal_sample_pool_backend != s_ctx.config.signal_sample_pool_backend) {
        ESP_RETURN_ON_FALSE(
            s_ctx.signal_pool_stats.allocated_blocks == 0U,
            ESP_ERR_INVALID_STATE,
            TAG,
            "signal sample pool backend cannot change while frames are queued"
        );
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
    ESP_RETURN_ON_FALSE(
        next.auth_mode == AETUS_AUTH_BEARER || next.auth_mode == AETUS_AUTH_HMAC_SHA256,
        ESP_ERR_INVALID_ARG,
        TAG,
        "unsupported auth mode"
    );
    ESP_RETURN_ON_FALSE(
        next.signal_sample_pool_backend == AETUS_SIGNAL_SAMPLE_POOL_STATIC ||
            next.signal_sample_pool_backend == AETUS_SIGNAL_SAMPLE_POOL_FREERTOS_HEAP,
        ESP_ERR_INVALID_ARG,
        TAG,
        "unsupported signal sample pool backend"
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
        esp_err_t err = ESP_OK;
        xEventGroupClearBits(s_ctx.events, AETUS_WIFI_CONNECTED_BIT);
        if (s_ctx.config.connected_led_enabled) {
            gpio_set_level((gpio_num_t)s_ctx.config.connected_led_gpio, 0);
        }
        s_ctx.wifi_reconfiguring = true;
        (void)esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(250));
        err = wifi_apply_sta_config(&s_ctx);
        s_ctx.wifi_reconfiguring = false;
        ESP_RETURN_ON_ERROR(err, TAG, "wifi config update failed");
        ESP_LOGI(TAG, "wifi reconnect after config update");
        ESP_RETURN_ON_ERROR(esp_wifi_connect(), TAG, "wifi reconnect after config update failed");
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

esp_err_t aetus_get_signal_sample_pool_stats(aetus_signal_sample_pool_stats_t *stats)
{
    ESP_RETURN_ON_FALSE(stats != NULL, ESP_ERR_INVALID_ARG, TAG, "signal sample pool stats output is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    aetus_signal_sample_pool_copy_stats(stats, &s_ctx.signal_pool_stats);
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
    if (xQueueSend(s_ctx.queue, &item, timeout) != pdTRUE) {
        ESP_LOGW(TAG, "telemetry enqueue failed because queue is full");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t aetus_enqueue_signal_frame(const aetus_signal_frame_t *frame, TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(frame != NULL, ESP_ERR_INVALID_ARG, TAG, "signal frame is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");
    ESP_RETURN_ON_ERROR(validate_signal_frame(frame), TAG, "signal frame validation failed");

    void *owner = NULL;
    uint8_t *samples = aetus_signal_sample_pool_alloc(
        s_ctx.config.signal_sample_pool_backend,
        &s_ctx.signal_pool_stats,
        frame->samples_size,
        &owner
    );
    ESP_RETURN_ON_FALSE(samples != NULL, ESP_ERR_NO_MEM, TAG, "signal sample pool allocation failed");
    memcpy(samples, frame->samples, frame->samples_size);

    aetus_queue_item_t item = {
        .kind = AETUS_QUEUE_ITEM_SIGNAL_FRAME,
        .body.signal_frame = *frame,
        .signal_sample_owner = owner,
    };
    item.body.signal_frame.samples = samples;
    if (xQueueSend(s_ctx.queue, &item, timeout) != pdTRUE) {
        aetus_signal_sample_pool_note_queue_send_failure(&s_ctx.signal_pool_stats);
        ESP_LOGW(
            TAG,
            "signal frame enqueue failed because queue is full; stream_key=%s samples_size=%u",
            item.body.signal_frame.stream_key,
            (unsigned)item.body.signal_frame.samples_size
        );
        release_queue_item(&s_ctx, &item);
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t aetus_enqueue_status(const aetus_status_t *status, TickType_t timeout)
{
    ESP_RETURN_ON_FALSE(status != NULL, ESP_ERR_INVALID_ARG, TAG, "status is required");
    ESP_RETURN_ON_FALSE(s_ctx.queue != NULL, ESP_ERR_INVALID_STATE, TAG, "aetus not started");

    aetus_queue_item_t item = {
        .kind = AETUS_QUEUE_ITEM_STATUS,
        .body.status = *status,
    };
    if (xQueueSend(s_ctx.queue, &item, timeout) != pdTRUE) {
        ESP_LOGW(TAG, "status enqueue failed because queue is full");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
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
