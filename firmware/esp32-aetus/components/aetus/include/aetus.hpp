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

    [[nodiscard]] const aetus_config_t &get() const
    {
        return value_;
    }

    [[nodiscard]] esp_err_t start() const
    {
        return aetus_start(&value_);
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
