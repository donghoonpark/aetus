#include "aetus.h"

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "nimble/ble.h"
#include "nimble/nimble_npl.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

static const char *TAG = "aetus_prov";

#define AETUS_PROVISIONING_ADV_INTERVAL_MS 3500U
#define AETUS_PROVISIONING_AUTO_DISCONNECT_MS (10U * 60U * 1000U)
#define AETUS_PROVISIONING_ADV_INTERVAL_UNITS ((AETUS_PROVISIONING_ADV_INTERVAL_MS * 1000U) / 625U)

typedef enum {
    AETUS_PROV_FIELD_WIFI_SSID = 1,
    AETUS_PROV_FIELD_WIFI_AUTH,
    AETUS_PROV_FIELD_WIFI_ID,
    AETUS_PROV_FIELD_WIFI_PASSWORD,
    AETUS_PROV_FIELD_INGEST_URL,
    AETUS_PROV_FIELD_TIME_URL,
    AETUS_PROV_FIELD_DEVICE_ID,
    AETUS_PROV_FIELD_DEVICE_TOKEN,
    AETUS_PROV_FIELD_FIRMWARE_VERSION,
    AETUS_PROV_FIELD_UPLOAD_INTERVAL_MS,
    AETUS_PROV_FIELD_QUEUE_DEPTH,
    AETUS_PROV_FIELD_LED_ENABLED,
    AETUS_PROV_FIELD_LED_GPIO,
    AETUS_PROV_FIELD_APPLY,
} aetus_provisioning_field_t;

typedef struct {
    aetus_config_t config;
    char wifi_ssid[AETUS_WIFI_SSID_MAX + 1];
    char wifi_password[AETUS_WIFI_PASSWORD_MAX + 1];
    char wifi_identity[AETUS_WIFI_IDENTITY_MAX + 1];
    char ingest_url[AETUS_URL_MAX + 1];
    char time_url[AETUS_URL_MAX + 1];
    char device_id[AETUS_DEVICE_ID_MAX + 1];
    char device_token[AETUS_DEVICE_TOKEN_MAX + 1];
    aetus_provisioning_config_changed_cb_t config_changed_cb;
    aetus_provisioning_connection_check_cb_t connection_check_cb;
    void *user_ctx;
    struct ble_npl_callout disconnect_callout;
    uint16_t conn_handle;
    uint8_t own_addr_type;
    bool started;
    bool connected;
    bool disconnect_callout_initialized;
} aetus_provisioning_state_t;

static aetus_provisioning_state_t s_prov;

static const ble_uuid128_t s_service_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x53, 0x56, 0x43, 0x00, 0x01);
static const ble_uuid128_t s_wifi_ssid_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x53, 0x53, 0x49, 0x44, 0x01);
static const ble_uuid128_t s_wifi_auth_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x41, 0x55, 0x54, 0x48, 0x01);
static const ble_uuid128_t s_wifi_id_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x57, 0x49, 0x44, 0x00, 0x01);
static const ble_uuid128_t s_wifi_password_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x57, 0x50, 0x41, 0x53, 0x01);
static const ble_uuid128_t s_ingest_url_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x49, 0x4e, 0x47, 0x00, 0x01);
static const ble_uuid128_t s_time_url_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x54, 0x49, 0x4d, 0x45, 0x01);
static const ble_uuid128_t s_device_id_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x44, 0x45, 0x56, 0x00, 0x01);
static const ble_uuid128_t s_device_token_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x54, 0x4f, 0x4b, 0x00, 0x01);
static const ble_uuid128_t s_firmware_version_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x46, 0x57, 0x56, 0x00, 0x01);
static const ble_uuid128_t s_upload_interval_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x55, 0x50, 0x4c, 0x44, 0x01);
static const ble_uuid128_t s_queue_depth_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x51, 0x55, 0x45, 0x55, 0x01);
static const ble_uuid128_t s_led_enabled_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x4c, 0x45, 0x44, 0x45, 0x01);
static const ble_uuid128_t s_led_gpio_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x4c, 0x47, 0x50, 0x49, 0x01);
static const ble_uuid128_t s_apply_uuid =
    BLE_UUID128_INIT(0x41, 0x45, 0x54, 0x55, 0x53, 0x2d, 0x50, 0x52, 0x4f, 0x56, 0x2d, 0x41, 0x50, 0x50, 0x4c, 0x01);
static const ble_uuid16_t s_cud_uuid = BLE_UUID16_INIT(0x2901);

static int provisioning_access(
    uint16_t conn_handle,
    uint16_t attr_handle,
    struct ble_gatt_access_ctxt *ctxt,
    void *arg
);
static int provisioning_gap_event(struct ble_gap_event *event, void *arg);
static void provisioning_disconnect_timeout(struct ble_npl_event *event);

#define PROV_CUD(name_value) \
    (struct ble_gatt_dsc_def[]) { \
        { \
            .uuid = &s_cud_uuid.u, \
            .access_cb = provisioning_access, \
            .arg = (void *)(name_value), \
            .att_flags = BLE_ATT_F_READ, \
        }, \
        {0}, \
    }

#define PROV_CHR(uuid_value, field_value, flags_value, name_value) \
    { \
        .uuid = &(uuid_value).u, \
        .access_cb = provisioning_access, \
        .arg = (void *)(uintptr_t)(field_value), \
        .flags = (flags_value), \
        .descriptors = PROV_CUD(name_value), \
    }

static const struct ble_gatt_svc_def s_provisioning_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &s_service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            PROV_CHR(s_wifi_ssid_uuid, AETUS_PROV_FIELD_WIFI_SSID, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "wifi_ssid"),
            PROV_CHR(s_wifi_auth_uuid, AETUS_PROV_FIELD_WIFI_AUTH, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "wifi_auth"),
            PROV_CHR(s_wifi_id_uuid, AETUS_PROV_FIELD_WIFI_ID, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "wifi_id"),
            PROV_CHR(s_wifi_password_uuid, AETUS_PROV_FIELD_WIFI_PASSWORD, BLE_GATT_CHR_F_WRITE, "wifi_password"),
            PROV_CHR(s_ingest_url_uuid, AETUS_PROV_FIELD_INGEST_URL, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "ingest_url"),
            PROV_CHR(s_time_url_uuid, AETUS_PROV_FIELD_TIME_URL, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "time_url"),
            PROV_CHR(s_device_id_uuid, AETUS_PROV_FIELD_DEVICE_ID, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "device_id"),
            PROV_CHR(s_device_token_uuid, AETUS_PROV_FIELD_DEVICE_TOKEN, BLE_GATT_CHR_F_WRITE, "device_token"),
            PROV_CHR(s_firmware_version_uuid, AETUS_PROV_FIELD_FIRMWARE_VERSION, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "firmware_version"),
            PROV_CHR(s_upload_interval_uuid, AETUS_PROV_FIELD_UPLOAD_INTERVAL_MS, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "upload_interval_ms"),
            PROV_CHR(s_queue_depth_uuid, AETUS_PROV_FIELD_QUEUE_DEPTH, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "queue_depth"),
            PROV_CHR(s_led_enabled_uuid, AETUS_PROV_FIELD_LED_ENABLED, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "led_enabled"),
            PROV_CHR(s_led_gpio_uuid, AETUS_PROV_FIELD_LED_GPIO, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE, "led_gpio"),
            PROV_CHR(s_apply_uuid, AETUS_PROV_FIELD_APPLY, BLE_GATT_CHR_F_WRITE, "apply"),
            {0},
        },
    },
    {0},
};

static void bind_pending_config(void)
{
    s_prov.config.wifi_ssid = s_prov.wifi_ssid;
    s_prov.config.wifi_password = s_prov.wifi_password;
    s_prov.config.wifi_identity = s_prov.wifi_identity;
    s_prov.config.ingest_url = s_prov.ingest_url;
    s_prov.config.time_url = s_prov.time_url;
    s_prov.config.device_id = s_prov.device_id;
    s_prov.config.device_token = s_prov.device_token;
}

static void copy_string_value(char *target, size_t target_size, const char *source)
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

static void sync_pending_from_runtime(void)
{
    aetus_config_t current = {0};
    bind_pending_config();
    if (aetus_get_config(&current) != ESP_OK) {
        return;
    }
    copy_string_value(s_prov.wifi_ssid, sizeof(s_prov.wifi_ssid), current.wifi_ssid);
    copy_string_value(s_prov.wifi_password, sizeof(s_prov.wifi_password), current.wifi_password);
    copy_string_value(s_prov.wifi_identity, sizeof(s_prov.wifi_identity), current.wifi_identity);
    copy_string_value(s_prov.ingest_url, sizeof(s_prov.ingest_url), current.ingest_url);
    copy_string_value(s_prov.time_url, sizeof(s_prov.time_url), current.time_url);
    copy_string_value(s_prov.device_id, sizeof(s_prov.device_id), current.device_id);
    copy_string_value(s_prov.device_token, sizeof(s_prov.device_token), current.device_token);
    s_prov.config.wifi_auth = current.wifi_auth;
    s_prov.config.firmware_version = current.firmware_version;
    s_prov.config.upload_interval_ms = current.upload_interval_ms;
    s_prov.config.queue_depth = current.queue_depth;
    s_prov.config.connected_led_enabled = current.connected_led_enabled;
    s_prov.config.connected_led_gpio = current.connected_led_gpio;
}

static int append_text(struct os_mbuf *om, const char *value)
{
    const char *text = value != NULL ? value : "";
    return os_mbuf_append(om, text, strlen(text)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int append_u32(struct os_mbuf *om, uint32_t value)
{
    char buffer[16];
    snprintf(buffer, sizeof(buffer), "%lu", (unsigned long)value);
    return append_text(om, buffer);
}

static int append_bool(struct os_mbuf *om, bool value)
{
    return append_text(om, value ? "1" : "0");
}

static int write_text(struct os_mbuf *om, char *target, size_t target_size)
{
    uint16_t len = OS_MBUF_PKTLEN(om);
    if (target_size == 0 || len >= target_size) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    int rc = ble_hs_mbuf_to_flat(om, target, target_size - 1, &len);
    if (rc != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    while (len > 0 && (target[len - 1] == '\n' || target[len - 1] == '\r')) {
        len--;
    }
    target[len] = '\0';
    return 0;
}

static int read_u32_from_mbuf(struct os_mbuf *om, uint32_t *value)
{
    char buffer[16] = {0};
    int rc = write_text(om, buffer, sizeof(buffer));
    if (rc != 0) {
        return rc;
    }
    errno = 0;
    char *end = NULL;
    unsigned long parsed = strtoul(buffer, &end, 10);
    if (errno != 0 || end == buffer || *end != '\0') {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    *value = (uint32_t)parsed;
    return 0;
}

static int read_bool_from_mbuf(struct os_mbuf *om, bool *value)
{
    char buffer[8] = {0};
    int rc = write_text(om, buffer, sizeof(buffer));
    if (rc != 0) {
        return rc;
    }
    if (strcmp(buffer, "1") == 0 || strcmp(buffer, "true") == 0 || strcmp(buffer, "on") == 0) {
        *value = true;
        return 0;
    }
    if (strcmp(buffer, "0") == 0 || strcmp(buffer, "false") == 0 || strcmp(buffer, "off") == 0) {
        *value = false;
        return 0;
    }
    return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
}

static int read_auth_from_mbuf(struct os_mbuf *om, aetus_wifi_auth_t *auth)
{
    char buffer[8] = {0};
    int rc = write_text(om, buffer, sizeof(buffer));
    if (rc != 0) {
        return rc;
    }
    if (strcmp(buffer, "psk") == 0 || strcmp(buffer, "0") == 0) {
        *auth = AETUS_WIFI_AUTH_PSK;
        return 0;
    }
    if (strcmp(buffer, "peap") == 0 || strcmp(buffer, "1") == 0) {
        *auth = AETUS_WIFI_AUTH_PEAP;
        return 0;
    }
    return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
}

static int provisioning_read(aetus_provisioning_field_t field, struct os_mbuf *om)
{
    switch (field) {
    case AETUS_PROV_FIELD_WIFI_SSID:
        return append_text(om, s_prov.wifi_ssid);
    case AETUS_PROV_FIELD_WIFI_AUTH:
        return append_text(om, s_prov.config.wifi_auth == AETUS_WIFI_AUTH_PEAP ? "peap" : "psk");
    case AETUS_PROV_FIELD_WIFI_ID:
        return append_text(om, s_prov.wifi_identity);
    case AETUS_PROV_FIELD_INGEST_URL:
        return append_text(om, s_prov.ingest_url);
    case AETUS_PROV_FIELD_TIME_URL:
        return append_text(om, s_prov.time_url);
    case AETUS_PROV_FIELD_DEVICE_ID:
        return append_text(om, s_prov.device_id);
    case AETUS_PROV_FIELD_FIRMWARE_VERSION:
        return append_u32(om, s_prov.config.firmware_version);
    case AETUS_PROV_FIELD_UPLOAD_INTERVAL_MS:
        return append_u32(om, s_prov.config.upload_interval_ms);
    case AETUS_PROV_FIELD_QUEUE_DEPTH:
        return append_u32(om, s_prov.config.queue_depth);
    case AETUS_PROV_FIELD_LED_ENABLED:
        return append_bool(om, s_prov.config.connected_led_enabled);
    case AETUS_PROV_FIELD_LED_GPIO:
        return append_u32(om, (uint32_t)s_prov.config.connected_led_gpio);
    default:
        return BLE_ATT_ERR_READ_NOT_PERMITTED;
    }
}

static int provisioning_write(aetus_provisioning_field_t field, struct os_mbuf *om)
{
    uint32_t number = 0;
    int rc = 0;

    switch (field) {
    case AETUS_PROV_FIELD_WIFI_SSID:
        rc = write_text(om, s_prov.wifi_ssid, sizeof(s_prov.wifi_ssid));
        if (rc == 0) {
            ESP_LOGI(TAG, "provisioning write wifi_ssid len=%u", (unsigned)strlen(s_prov.wifi_ssid));
        }
        return rc;
    case AETUS_PROV_FIELD_WIFI_AUTH:
        rc = read_auth_from_mbuf(om, &s_prov.config.wifi_auth);
        if (rc == 0) {
            ESP_LOGI(TAG, "provisioning write wifi_auth=%s", s_prov.config.wifi_auth == AETUS_WIFI_AUTH_PEAP ? "peap" : "psk");
        }
        return rc;
    case AETUS_PROV_FIELD_WIFI_ID:
        rc = write_text(om, s_prov.wifi_identity, sizeof(s_prov.wifi_identity));
        if (rc == 0) {
            ESP_LOGI(TAG, "provisioning write wifi_id len=%u", (unsigned)strlen(s_prov.wifi_identity));
        }
        return rc;
    case AETUS_PROV_FIELD_WIFI_PASSWORD:
        rc = write_text(om, s_prov.wifi_password, sizeof(s_prov.wifi_password));
        if (rc == 0) {
            ESP_LOGI(TAG, "provisioning write wifi_password len=%u", (unsigned)strlen(s_prov.wifi_password));
        }
        return rc;
    case AETUS_PROV_FIELD_INGEST_URL:
        return write_text(om, s_prov.ingest_url, sizeof(s_prov.ingest_url));
    case AETUS_PROV_FIELD_TIME_URL:
        return write_text(om, s_prov.time_url, sizeof(s_prov.time_url));
    case AETUS_PROV_FIELD_DEVICE_ID:
        return write_text(om, s_prov.device_id, sizeof(s_prov.device_id));
    case AETUS_PROV_FIELD_DEVICE_TOKEN:
        return write_text(om, s_prov.device_token, sizeof(s_prov.device_token));
    case AETUS_PROV_FIELD_FIRMWARE_VERSION:
        rc = read_u32_from_mbuf(om, &number);
        s_prov.config.firmware_version = number;
        return rc;
    case AETUS_PROV_FIELD_UPLOAD_INTERVAL_MS:
        rc = read_u32_from_mbuf(om, &number);
        s_prov.config.upload_interval_ms = number;
        return rc;
    case AETUS_PROV_FIELD_QUEUE_DEPTH:
        rc = read_u32_from_mbuf(om, &number);
        s_prov.config.queue_depth = number;
        return rc;
    case AETUS_PROV_FIELD_LED_ENABLED:
        return read_bool_from_mbuf(om, &s_prov.config.connected_led_enabled);
    case AETUS_PROV_FIELD_LED_GPIO:
        rc = read_u32_from_mbuf(om, &number);
        s_prov.config.connected_led_gpio = (int)number;
        return rc;
    case AETUS_PROV_FIELD_APPLY:
        ESP_LOGI(
            TAG,
            "provisioning apply ssid=%s auth=%s password_len=%u",
            s_prov.wifi_ssid,
            s_prov.config.wifi_auth == AETUS_WIFI_AUTH_PEAP ? "peap" : "psk",
            (unsigned)strlen(s_prov.wifi_password)
        );
        rc = aetus_update_config(&s_prov.config);
        if (rc != ESP_OK) {
            ESP_LOGE(TAG, "config apply failed: %s", esp_err_to_name(rc));
            return BLE_ATT_ERR_UNLIKELY;
        }
        ESP_LOGI(TAG, "provisioning apply complete");
        if (s_prov.config_changed_cb != NULL) {
            s_prov.config_changed_cb(&s_prov.config, s_prov.user_ctx);
        }
        return 0;
    default:
        return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    }
}

static int provisioning_access(
    uint16_t conn_handle,
    uint16_t attr_handle,
    struct ble_gatt_access_ctxt *ctxt,
    void *arg
)
{
    (void)conn_handle;
    (void)attr_handle;

    switch (ctxt->op) {
    case BLE_GATT_ACCESS_OP_READ_CHR:
        return provisioning_read((aetus_provisioning_field_t)(uintptr_t)arg, ctxt->om);
    case BLE_GATT_ACCESS_OP_WRITE_CHR:
        return provisioning_write((aetus_provisioning_field_t)(uintptr_t)arg, ctxt->om);
    case BLE_GATT_ACCESS_OP_READ_DSC:
        return append_text(ctxt->om, (const char *)arg);
    default:
        return BLE_ATT_ERR_UNLIKELY;
    }
}

static void provisioning_advertise(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)ble_svc_gap_device_name();
    fields.name_len = strlen((const char *)fields.name);
    fields.name_is_complete = 1;
    fields.uuids128 = &s_service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising field setup failed rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params adv_params = {0};
    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    adv_params.itvl_min = AETUS_PROVISIONING_ADV_INTERVAL_UNITS;
    adv_params.itvl_max = AETUS_PROVISIONING_ADV_INTERVAL_UNITS;
    rc = ble_gap_adv_start(s_prov.own_addr_type, NULL, BLE_HS_FOREVER, &adv_params, provisioning_gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "advertising start failed rc=%d", rc);
        return;
    }
    ESP_LOGI(TAG, "advertising interval set to %u ms", AETUS_PROVISIONING_ADV_INTERVAL_MS);
}

static void notify_connection_check(uint16_t conn_handle, int status)
{
    if (s_prov.connection_check_cb == NULL) {
        return;
    }
    struct ble_gap_conn_desc desc = {0};
    int rc = ble_gap_conn_find(conn_handle, &desc);
    if (rc != 0) {
        s_prov.connection_check_cb(conn_handle, status, 0, 0, 0, s_prov.user_ctx);
        return;
    }
    s_prov.connection_check_cb(
        conn_handle,
        status,
        desc.conn_itvl,
        desc.conn_latency,
        desc.supervision_timeout,
        s_prov.user_ctx
    );
}

static void provisioning_start_disconnect_timer(uint16_t conn_handle)
{
    if (!s_prov.disconnect_callout_initialized) {
        return;
    }
    s_prov.conn_handle = conn_handle;
    ble_npl_callout_stop(&s_prov.disconnect_callout);
    ble_npl_callout_reset(
        &s_prov.disconnect_callout,
        ble_npl_time_ms_to_ticks32(AETUS_PROVISIONING_AUTO_DISCONNECT_MS)
    );
    ESP_LOGI(
        TAG,
        "provisioning auto-disconnect armed conn=%u timeout_ms=%u",
        conn_handle,
        AETUS_PROVISIONING_AUTO_DISCONNECT_MS
    );
}

static void provisioning_stop_disconnect_timer(void)
{
    if (!s_prov.disconnect_callout_initialized) {
        return;
    }
    ble_npl_callout_stop(&s_prov.disconnect_callout);
}

static void provisioning_disconnect_timeout(struct ble_npl_event *event)
{
    (void)event;
    if (!s_prov.connected) {
        return;
    }
    ESP_LOGI(TAG, "provisioning auto-disconnect timeout conn=%u", s_prov.conn_handle);
    int rc = ble_gap_terminate(s_prov.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    if (rc != 0) {
        ESP_LOGW(TAG, "provisioning auto-disconnect failed rc=%d", rc);
    }
}

static int provisioning_gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        ESP_LOGI(TAG, "provisioning client connect status=%d", event->connect.status);
        if (event->connect.status == 0) {
            s_prov.connected = true;
            provisioning_start_disconnect_timer(event->connect.conn_handle);
            notify_connection_check(event->connect.conn_handle, event->connect.status);
        } else {
            provisioning_advertise();
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "provisioning client disconnect reason=%d", event->disconnect.reason);
        s_prov.connected = false;
        provisioning_stop_disconnect_timer();
        provisioning_advertise();
        return 0;
    case BLE_GAP_EVENT_CONN_UPDATE:
        ESP_LOGI(TAG, "provisioning connection updated status=%d", event->conn_update.status);
        notify_connection_check(event->conn_update.conn_handle, event->conn_update.status);
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        provisioning_advertise();
        return 0;
    default:
        return 0;
    }
}

static void provisioning_on_sync(void)
{
    int rc = ble_hs_id_infer_auto(0, &s_prov.own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "address type infer failed rc=%d", rc);
        return;
    }
    provisioning_advertise();
}

static void provisioning_on_reset(int reason)
{
    ESP_LOGE(TAG, "nimble reset reason=%d", reason);
}

static void provisioning_host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static esp_err_t ensure_nvs_initialized(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "nvs erase failed");
        err = nvs_flash_init();
    }
    return err == ESP_ERR_INVALID_STATE ? ESP_OK : err;
}

esp_err_t aetus_start_provisioning(const aetus_provisioning_config_t *config)
{
    ESP_RETURN_ON_FALSE(!s_prov.started, ESP_ERR_INVALID_STATE, TAG, "provisioning already started");

    memset(&s_prov, 0, sizeof(s_prov));
    bind_pending_config();
    sync_pending_from_runtime();
    s_prov.config_changed_cb = config != NULL ? config->config_changed_cb : NULL;
    s_prov.connection_check_cb = config != NULL ? config->connection_check_cb : NULL;
    s_prov.user_ctx = config != NULL ? config->user_ctx : NULL;

    ESP_RETURN_ON_ERROR(ensure_nvs_initialized(), TAG, "nvs init failed");
    esp_err_t err = nimble_port_init();
    ESP_RETURN_ON_ERROR(err, TAG, "nimble init failed");
    ble_npl_callout_init(
        &s_prov.disconnect_callout,
        nimble_port_get_dflt_eventq(),
        provisioning_disconnect_timeout,
        NULL
    );
    s_prov.disconnect_callout_initialized = true;

    ble_hs_cfg.reset_cb = provisioning_on_reset;
    ble_hs_cfg.sync_cb = provisioning_on_sync;
    ble_svc_gap_init();
    ble_svc_gatt_init();

    int rc = ble_gatts_count_cfg(s_provisioning_svcs);
    ESP_RETURN_ON_FALSE(rc == 0, ESP_FAIL, TAG, "gatt count failed");
    rc = ble_gatts_add_svcs(s_provisioning_svcs);
    ESP_RETURN_ON_FALSE(rc == 0, ESP_FAIL, TAG, "gatt service add failed");

    const char *device_name = config != NULL && config->device_name != NULL ? config->device_name : "AETUS Provisioning";
    rc = ble_svc_gap_device_name_set(device_name);
    ESP_RETURN_ON_FALSE(rc == 0, ESP_FAIL, TAG, "gap device name failed");

    nimble_port_freertos_init(provisioning_host_task);
    s_prov.started = true;
    return ESP_OK;
}
