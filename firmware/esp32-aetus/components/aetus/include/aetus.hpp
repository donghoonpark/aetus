#pragma once

#include "aetus.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <span>
#include <string_view>

namespace aetus {

class Config {
public:
    constexpr Config() = default;

    constexpr Config &wifi(const char *ssid, const char *password)
    {
        value_.wifi_ssid = ssid;
        value_.wifi_password = password;
        value_.wifi_auth = AETUS_WIFI_AUTH_PSK;
        value_.wifi_identity = nullptr;
        return *this;
    }

    constexpr Config &wifi_peap(const char *ssid, const char *identity, const char *password)
    {
        value_.wifi_ssid = ssid;
        value_.wifi_password = password;
        value_.wifi_auth = AETUS_WIFI_AUTH_PEAP;
        value_.wifi_identity = identity;
        return *this;
    }

    constexpr Config &ingest_url(const char *url)
    {
        value_.ingest_url = url;
        return *this;
    }

    constexpr Config &time_url(const char *url)
    {
        value_.time_url = url;
        return *this;
    }

    constexpr Config &device(const char *device_id, const char *device_token)
    {
        value_.device_id = device_id;
        value_.device_token = device_token;
        return *this;
    }

    constexpr Config &bearer_auth()
    {
        value_.auth_mode = AETUS_AUTH_BEARER;
        return *this;
    }

    constexpr Config &hmac_sha256_auth()
    {
        value_.auth_mode = AETUS_AUTH_HMAC_SHA256;
        return *this;
    }

    constexpr Config &firmware_version(uint32_t version)
    {
        value_.firmware_version = version;
        return *this;
    }

    constexpr Config &upload_interval_ms(uint32_t interval_ms)
    {
        value_.upload_interval_ms = interval_ms;
        return *this;
    }

    constexpr Config &queue_depth(uint32_t depth)
    {
        value_.queue_depth = depth;
        return *this;
    }

    constexpr Config &static_signal_sample_pool()
    {
        value_.signal_sample_pool_backend = AETUS_SIGNAL_SAMPLE_POOL_STATIC;
        return *this;
    }

    constexpr Config &freertos_heap_signal_sample_pool()
    {
        value_.signal_sample_pool_backend = AETUS_SIGNAL_SAMPLE_POOL_FREERTOS_HEAP;
        return *this;
    }

    constexpr Config &connected_led(int gpio)
    {
        value_.connected_led_enabled = true;
        value_.connected_led_gpio = gpio;
        return *this;
    }

    constexpr Config &disable_connected_led()
    {
        value_.connected_led_enabled = false;
        value_.connected_led_gpio = 0;
        return *this;
    }

    [[nodiscard]] const aetus_config_t &get() const
    {
        return value_;
    }

    [[nodiscard]] esp_err_t start() const
    {
        return aetus_start(&value_);
    }

    [[nodiscard]] esp_err_t apply() const
    {
        return aetus_update_config(&value_);
    }

private:
    aetus_config_t value_{};
};

inline esp_err_t sync_rtc(TickType_t timeout)
{
    return aetus_sync_rtc(timeout);
}

class Telemetry {
public:
    Telemetry()
    {
        aetus_telemetry_init(&value_);
    }

    Telemetry &timestamp(uint64_t timestamp_ns)
    {
        value_.timestamp_ns = timestamp_ns;
        return *this;
    }

    Telemetry &timestamp_from_rtc()
    {
        remember(try_timestamp_from_rtc());
        return *this;
    }

    esp_err_t try_timestamp_from_rtc()
    {
        return aetus_telemetry_set_timestamp_rtc(&value_);
    }

    Telemetry &add_int64(std::string_view key, int64_t value, std::string_view unit = {})
    {
        aetus_metric_t *metric = append_metric(key, unit);
        if (metric == nullptr) {
            return *this;
        }
        metric->type = AETUS_METRIC_VALUE_INT64;
        metric->value.int64_value = value;
        return *this;
    }

    Telemetry &add_double(std::string_view key, double value, std::string_view unit = {})
    {
        aetus_metric_t *metric = append_metric(key, unit);
        if (metric == nullptr) {
            return *this;
        }
        metric->type = AETUS_METRIC_VALUE_DOUBLE;
        metric->value.double_value = value;
        return *this;
    }

    Telemetry &add_bool(std::string_view key, bool value, std::string_view unit = {})
    {
        aetus_metric_t *metric = append_metric(key, unit);
        if (metric == nullptr) {
            return *this;
        }
        metric->type = AETUS_METRIC_VALUE_BOOL;
        metric->value.bool_value = value;
        return *this;
    }

    Telemetry &add_string(std::string_view key, std::string_view value, std::string_view unit = {})
    {
        aetus_metric_t *metric = append_metric(key, unit);
        if (metric == nullptr) {
            return *this;
        }
        metric->type = AETUS_METRIC_VALUE_STRING;
        if (!copy_string(metric->value.string_value, value)) {
            remember(ESP_ERR_INVALID_ARG);
        }
        return *this;
    }

    Telemetry &add_bytes(std::string_view key, std::span<const uint8_t> value, std::string_view unit = {})
    {
        aetus_metric_t *metric = append_metric(key, unit);
        if (metric == nullptr) {
            return *this;
        }
        if (value.size() > AETUS_METRIC_BYTES_MAX) {
            remember(ESP_ERR_INVALID_ARG);
            return *this;
        }
        metric->type = AETUS_METRIC_VALUE_BYTES;
        std::memcpy(metric->value.bytes_value.data, value.data(), value.size());
        metric->value.bytes_value.size = value.size();
        return *this;
    }

    [[nodiscard]] const aetus_telemetry_t &get() const
    {
        return value_;
    }

    [[nodiscard]] esp_err_t error() const
    {
        return error_;
    }

    [[nodiscard]] esp_err_t enqueue(TickType_t timeout) const
    {
        if (error_ != ESP_OK) {
            return error_;
        }
        return aetus_enqueue_telemetry(&value_, timeout);
    }

#ifdef CONFIG_AETUS_ISR_SAFE_ENQUEUE
    [[nodiscard]] esp_err_t enqueue_from_isr(BaseType_t *higher_priority_task_woken) const
    {
        if (error_ != ESP_OK) {
            return error_;
        }
        return aetus_enqueue_telemetry_from_isr(&value_, higher_priority_task_woken);
    }
#endif

private:
    template <size_t N>
    static bool copy_string(char (&target)[N], std::string_view source)
    {
        static_assert(N > 0);
        if (source.size() >= N) {
            target[0] = '\0';
            return false;
        }
        std::memcpy(target, source.data(), source.size());
        target[source.size()] = '\0';
        return true;
    }

    void remember(esp_err_t err)
    {
        if (error_ == ESP_OK && err != ESP_OK) {
            error_ = err;
        }
    }

    aetus_metric_t *append_metric(std::string_view key, std::string_view unit)
    {
        if (value_.metric_count >= AETUS_MAX_METRICS || key.empty()) {
            remember(ESP_ERR_INVALID_ARG);
            return nullptr;
        }

        aetus_metric_t *metric = &value_.metrics[value_.metric_count];
        if (!copy_string(metric->key, key) || !copy_string(metric->unit, unit)) {
            remember(ESP_ERR_INVALID_ARG);
            return nullptr;
        }

        value_.metric_count++;
        return metric;
    }

    aetus_telemetry_t value_{};
    esp_err_t error_{ESP_OK};
};

class SignalFrame {
public:
    SignalFrame()
    {
        aetus_signal_frame_init(&value_);
    }

    SignalFrame &timestamp(uint64_t timestamp_ns)
    {
        value_.timestamp_ns = timestamp_ns;
        return *this;
    }

    SignalFrame &timestamp_from_rtc()
    {
        remember(try_timestamp_from_rtc());
        return *this;
    }

    esp_err_t try_timestamp_from_rtc()
    {
        return aetus_signal_frame_set_timestamp_rtc(&value_);
    }

    SignalFrame &stream_key(std::string_view stream_key_value)
    {
        char buffer[AETUS_SIGNAL_STREAM_KEY_MAX] = {};
        if (!copy_string(buffer, stream_key_value)) {
            remember(ESP_ERR_INVALID_ARG);
            return *this;
        }
        remember(aetus_signal_frame_set_stream_key(&value_, buffer));
        return *this;
    }

    SignalFrame &sample_interval_ns(uint64_t interval_ns)
    {
        value_.sample_interval_ns = interval_ns;
        return *this;
    }

    SignalFrame &sample_count(uint32_t count)
    {
        value_.sample_count = count;
        return *this;
    }

    SignalFrame &encoding_float32_le()
    {
        value_.encoding = AETUS_SIGNAL_ENCODING_FLOAT32_LE;
        return *this;
    }

    SignalFrame &encoding_int16_le()
    {
        value_.encoding = AETUS_SIGNAL_ENCODING_INT16_LE;
        return *this;
    }

    SignalFrame &encoding_uint16_le()
    {
        value_.encoding = AETUS_SIGNAL_ENCODING_UINT16_LE;
        return *this;
    }

    SignalFrame &encoding_int32_le()
    {
        value_.encoding = AETUS_SIGNAL_ENCODING_INT32_LE;
        return *this;
    }

    SignalFrame &layout_interleaved()
    {
        value_.layout = AETUS_SIGNAL_LAYOUT_INTERLEAVED;
        return *this;
    }

    SignalFrame &layout_planar()
    {
        value_.layout = AETUS_SIGNAL_LAYOUT_PLANAR;
        return *this;
    }

    SignalFrame &add_channel(std::string_view key, std::string_view unit = {})
    {
        return add_channel_internal(key, unit, nullptr, nullptr);
    }

    SignalFrame &add_channel_with_scale(std::string_view key, std::string_view unit, float scale)
    {
        return add_channel_internal(key, unit, &scale, nullptr);
    }

    SignalFrame &add_channel_with_affine(std::string_view key, std::string_view unit, float scale, float offset)
    {
        return add_channel_internal(key, unit, &scale, &offset);
    }

    SignalFrame &set_sample_bytes(std::span<const uint8_t> bytes)
    {
        remember(aetus_signal_frame_set_samples(&value_, bytes.data(), bytes.size()));
        return *this;
    }

    template <typename T>
    SignalFrame &set_samples(std::span<const T> samples)
    {
        const auto *bytes = reinterpret_cast<const uint8_t *>(samples.data());
        const size_t byte_size = samples.size_bytes();
        remember(aetus_signal_frame_set_samples(&value_, bytes, byte_size));
        return *this;
    }

    [[nodiscard]] const aetus_signal_frame_t &get() const
    {
        return value_;
    }

    [[nodiscard]] esp_err_t error() const
    {
        return error_;
    }

    [[nodiscard]] esp_err_t enqueue(TickType_t timeout) const
    {
        if (error_ != ESP_OK) {
            return error_;
        }
        return aetus_enqueue_signal_frame(&value_, timeout);
    }

private:
    void remember(esp_err_t err)
    {
        if (error_ == ESP_OK && err != ESP_OK) {
            error_ = err;
        }
    }

    SignalFrame &add_channel_internal(
        std::string_view key,
        std::string_view unit,
        const float *scale,
        const float *offset
    )
    {
        char key_buffer[AETUS_METRIC_KEY_MAX] = {};
        char unit_buffer[AETUS_METRIC_UNIT_MAX] = {};
        if (!copy_string(key_buffer, key) || !copy_string(unit_buffer, unit)) {
            remember(ESP_ERR_INVALID_ARG);
            return *this;
        }
        remember(aetus_signal_frame_add_channel(&value_, key_buffer, unit_buffer, scale, offset));
        return *this;
    }

    template <size_t N>
    static bool copy_string(char (&target)[N], std::string_view source)
    {
        static_assert(N > 0);
        if (source.size() >= N) {
            target[0] = '\0';
            return false;
        }
        std::memcpy(target, source.data(), source.size());
        target[source.size()] = '\0';
        return true;
    }

    aetus_signal_frame_t value_{};
    esp_err_t error_{ESP_OK};
};

class Status {
public:
    explicit Status(aetus_device_status_t status = AETUS_DEVICE_STATUS_ONLINE)
    {
        aetus_status_init(&value_, status);
    }

    static Status online()
    {
        return Status(AETUS_DEVICE_STATUS_ONLINE);
    }

    static Status degraded()
    {
        return Status(AETUS_DEVICE_STATUS_DEGRADED);
    }

    static Status offline()
    {
        return Status(AETUS_DEVICE_STATUS_OFFLINE);
    }

    Status &rssi(int32_t value)
    {
        value_.rssi = value;
        return *this;
    }

    Status &free_heap(uint32_t value)
    {
        value_.free_heap = value;
        return *this;
    }

    Status &reboot_reason(std::string_view reason)
    {
        char buffer[sizeof(value_.reboot_reason)] = {};
        if (!copy_string(buffer, reason)) {
            remember(ESP_ERR_INVALID_ARG);
            return *this;
        }
        remember(aetus_status_set_reboot_reason(&value_, buffer));
        return *this;
    }

    Status &timestamp(uint64_t timestamp_ns)
    {
        value_.timestamp_ns = timestamp_ns;
        return *this;
    }

    Status &timestamp_from_rtc()
    {
        remember(try_timestamp_from_rtc());
        return *this;
    }

    esp_err_t try_timestamp_from_rtc()
    {
        return aetus_status_set_timestamp_rtc(&value_);
    }

    [[nodiscard]] const aetus_status_t &get() const
    {
        return value_;
    }

    [[nodiscard]] esp_err_t error() const
    {
        return error_;
    }

    [[nodiscard]] esp_err_t enqueue(TickType_t timeout) const
    {
        if (error_ != ESP_OK) {
            return error_;
        }
        return aetus_enqueue_status(&value_, timeout);
    }

#ifdef CONFIG_AETUS_ISR_SAFE_ENQUEUE
    [[nodiscard]] esp_err_t enqueue_from_isr(BaseType_t *higher_priority_task_woken) const
    {
        if (error_ != ESP_OK) {
            return error_;
        }
        return aetus_enqueue_status_from_isr(&value_, higher_priority_task_woken);
    }
#endif

private:
    template <size_t N>
    static bool copy_string(char (&target)[N], std::string_view source)
    {
        static_assert(N > 0);
        if (source.size() >= N) {
            target[0] = '\0';
            return false;
        }
        std::memcpy(target, source.data(), source.size());
        target[source.size()] = '\0';
        return true;
    }

    void remember(esp_err_t err)
    {
        if (error_ == ESP_OK && err != ESP_OK) {
            error_ = err;
        }
    }

    aetus_status_t value_{};
    esp_err_t error_{ESP_OK};
};

} // namespace aetus
