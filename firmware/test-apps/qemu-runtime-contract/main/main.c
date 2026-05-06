#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "aetus.h"
#include "aetus_internal.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TEST_QUEUE_DEPTH 4U
#define STRESS_TELEMETRY_TASKS 2U
#define STRESS_SIGNAL_TASKS 1U
#define STRESS_STATUS_TASKS 1U
#define STRESS_ITERATIONS 10U
#define STRESS_FLUSH_ITERATIONS 18U

static const char *TAG = "qemu_runtime_contract";
static volatile uint32_t s_stress_tasks_done;
static volatile uint32_t s_stress_flush_done;
static volatile bool s_stress_stop_flush;

static void print_result(const char *name, bool pass)
{
    printf("AETUS_RUNTIME_%s_%s\n", name, pass ? "PASS" : "FAIL");
    fflush(stdout);
}

static void apply_hooks(esp_err_t post_result, esp_err_t time_result, bool fake_time)
{
    aetus_test_runtime_hooks_t hooks = {
        .bypass_wifi = true,
        .fake_post = true,
        .fake_post_result = post_result,
        .fake_time = fake_time,
        .fake_time_result = time_result,
        .fake_time_ns = 1712345678901235000ULL,
    };
    aetus_test_set_runtime_hooks(&hooks);
}

static esp_err_t add_dynamic_metrics(aetus_telemetry_t *telemetry, uint32_t base)
{
    static const uint8_t bytes[] = {0xde, 0xad, 0xbe, 0xef, 0x42};
    char heap_string[24];
    snprintf(heap_string, sizeof(heap_string), "heap-%lu", (unsigned long)base);

    ESP_RETURN_ON_ERROR(aetus_telemetry_add_int64(telemetry, "m0", (int64_t)base, "u"), TAG, "m0 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_double(telemetry, "m1", 22.5 + (double)base, "c"), TAG, "m1 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_bool(telemetry, "m2", (base % 2U) == 0U, "b"), TAG, "m2 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_string(telemetry, "m3", "inline", "s"), TAG, "m3 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_string(telemetry, "m4", heap_string, "s"), TAG, "m4 failed");
    ESP_RETURN_ON_ERROR(aetus_telemetry_add_bytes(telemetry, "m5", bytes, sizeof(bytes), "b"), TAG, "m5 failed");
    return ESP_OK;
}

static esp_err_t enqueue_dynamic_telemetry(uint32_t base, TickType_t timeout)
{
    aetus_telemetry_t telemetry;
    aetus_telemetry_init(&telemetry);
    telemetry.timestamp_ns = 1712345678901235000ULL + base;
    esp_err_t err = add_dynamic_metrics(&telemetry, base);
    if (err == ESP_OK) {
        err = aetus_enqueue_telemetry(&telemetry, timeout);
    }
    aetus_telemetry_deinit(&telemetry);
    return err;
}

static esp_err_t fill_status_queue(void)
{
    for (uint32_t i = 0; i < TEST_QUEUE_DEPTH; i++) {
        aetus_status_t status;
        aetus_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
        status.free_heap = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_8BIT);
        ESP_RETURN_ON_ERROR(aetus_status_set_reboot_reason(&status, "queue-fill"), TAG, "status reason failed");
        ESP_RETURN_ON_ERROR(aetus_enqueue_status(&status, 0), TAG, "status fill failed");
    }
    return ESP_OK;
}

static esp_err_t make_signal_frame(aetus_signal_frame_t *frame, uint8_t *samples, size_t sample_size, uint32_t seed)
{
    for (size_t i = 0; i < sample_size; i++) {
        samples[i] = (uint8_t)(seed + i);
    }
    aetus_signal_frame_init(frame);
    frame->timestamp_ns = 1712345678901235000ULL + seed;
    frame->sample_interval_ns = 5000000ULL;
    frame->sample_count = (uint32_t)(sample_size / (2U * 2U));
    frame->encoding = AETUS_SIGNAL_ENCODING_INT16_LE;
    frame->layout = AETUS_SIGNAL_LAYOUT_INTERLEAVED;
    ESP_RETURN_ON_ERROR(aetus_signal_frame_set_stream_key(frame, "stress.signal"), TAG, "stream key failed");
    ESP_RETURN_ON_ERROR(aetus_signal_frame_add_channel(frame, "x", "raw", NULL, NULL), TAG, "channel x failed");
    ESP_RETURN_ON_ERROR(aetus_signal_frame_add_channel(frame, "y", "raw", NULL, NULL), TAG, "channel y failed");
    ESP_RETURN_ON_ERROR(aetus_signal_frame_set_samples(frame, samples, sample_size), TAG, "samples failed");
    return ESP_OK;
}

static esp_err_t enqueue_signal_frame(uint32_t seed, TickType_t timeout)
{
    uint8_t samples[80];
    aetus_signal_frame_t frame;
    ESP_RETURN_ON_ERROR(make_signal_frame(&frame, samples, sizeof(samples), seed), TAG, "make signal failed");
    return aetus_enqueue_signal_frame(&frame, timeout);
}

static bool test_telemetry_full_release(void)
{
    aetus_test_reset_release_stats();
    aetus_test_runtime_hooks_t hooks_before;
    aetus_test_get_runtime_hooks(&hooks_before);

    esp_err_t enqueue_err = enqueue_dynamic_telemetry(100U, pdMS_TO_TICKS(100));
    esp_err_t flush_err = aetus_flush(pdMS_TO_TICKS(10000));

    aetus_test_release_stats_t release_stats;
    aetus_test_runtime_hooks_t hooks_after;
    aetus_test_get_release_stats(&release_stats);
    aetus_test_get_runtime_hooks(&hooks_after);

    bool pass =
        enqueue_err == ESP_OK &&
        flush_err == ESP_OK &&
        release_stats.telemetry_heap_metrics_released == 1U &&
        release_stats.telemetry_blobs_released == 3U &&
        hooks_after.fake_post_count == hooks_before.fake_post_count + 1U;

    printf(
        "AETUS_RUNTIME_TELEMETRY_RELEASE_STATS enqueue=%s flush=%s heap_released=%lu blobs_released=%lu post_delta=%lu\n",
        esp_err_to_name(enqueue_err),
        esp_err_to_name(flush_err),
        (unsigned long)release_stats.telemetry_heap_metrics_released,
        (unsigned long)release_stats.telemetry_blobs_released,
        (unsigned long)(hooks_after.fake_post_count - hooks_before.fake_post_count)
    );
    print_result("TELEMETRY_RELEASE", pass);
    return pass;
}

static bool test_signal_enqueue_failure_release(void)
{
    aetus_signal_sample_pool_stats_t before;
    aetus_signal_sample_pool_stats_t after;
    ESP_ERROR_CHECK(aetus_get_signal_sample_pool_stats(&before));

    esp_err_t fill_err = fill_status_queue();
    esp_err_t signal_err = enqueue_signal_frame(200U, 0);
    ESP_ERROR_CHECK(aetus_flush(pdMS_TO_TICKS(10000)));
    ESP_ERROR_CHECK(aetus_get_signal_sample_pool_stats(&after));

    bool pass =
        fill_err == ESP_OK &&
        signal_err == ESP_ERR_TIMEOUT &&
        after.allocated_blocks == before.allocated_blocks &&
        after.allocation_count == before.allocation_count + 1U &&
        after.release_count == before.release_count + 1U &&
        after.queue_send_failure_release_count == before.queue_send_failure_release_count + 1U;

    printf(
        "AETUS_RUNTIME_SIGNAL_FAIL_STATS fill=%s signal=%s allocated=%lu alloc_delta=%lu release_delta=%lu qfail_delta=%lu\n",
        esp_err_to_name(fill_err),
        esp_err_to_name(signal_err),
        (unsigned long)after.allocated_blocks,
        (unsigned long)(after.allocation_count - before.allocation_count),
        (unsigned long)(after.release_count - before.release_count),
        (unsigned long)(after.queue_send_failure_release_count - before.queue_send_failure_release_count)
    );
    print_result("SIGNAL_FAIL_RELEASE", pass);
    return pass;
}

static void telemetry_stress_task(void *arg)
{
    uint32_t id = (uint32_t)(uintptr_t)arg;
    for (uint32_t i = 0; i < STRESS_ITERATIONS; i++) {
        (void)enqueue_dynamic_telemetry((id * 1000U) + i, pdMS_TO_TICKS(20));
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    __atomic_fetch_add(&s_stress_tasks_done, 1U, __ATOMIC_SEQ_CST);
    vTaskDelete(NULL);
}

static void signal_stress_task(void *arg)
{
    uint32_t id = (uint32_t)(uintptr_t)arg;
    for (uint32_t i = 0; i < STRESS_ITERATIONS; i++) {
        (void)enqueue_signal_frame((id * 1000U) + i, pdMS_TO_TICKS(20));
        vTaskDelay(pdMS_TO_TICKS(3));
    }
    __atomic_fetch_add(&s_stress_tasks_done, 1U, __ATOMIC_SEQ_CST);
    vTaskDelete(NULL);
}

static void status_stress_task(void *arg)
{
    uint32_t id = (uint32_t)(uintptr_t)arg;
    for (uint32_t i = 0; i < STRESS_ITERATIONS; i++) {
        aetus_status_t status;
        aetus_status_init(&status, AETUS_DEVICE_STATUS_ONLINE);
        status.free_heap = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_8BIT);
        status.rssi = -45 - (int32_t)i;
        (void)aetus_status_set_reboot_reason(&status, "stress");
        (void)aetus_enqueue_status(&status, pdMS_TO_TICKS(20));
        vTaskDelay(pdMS_TO_TICKS(4 + id));
    }
    __atomic_fetch_add(&s_stress_tasks_done, 1U, __ATOMIC_SEQ_CST);
    vTaskDelete(NULL);
}

static void flush_stress_task(void *arg)
{
    (void)arg;
    for (uint32_t i = 0; i < STRESS_FLUSH_ITERATIONS && !s_stress_stop_flush; i++) {
        (void)aetus_flush(pdMS_TO_TICKS(250));
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    __atomic_store_n(&s_stress_flush_done, 1U, __ATOMIC_SEQ_CST);
    vTaskDelete(NULL);
}

static bool test_concurrency_stress(void)
{
    const uint32_t total_tasks = STRESS_TELEMETRY_TASKS + STRESS_SIGNAL_TASKS + STRESS_STATUS_TASKS;
    s_stress_tasks_done = 0;
    s_stress_flush_done = 0;
    s_stress_stop_flush = false;
    aetus_test_reset_release_stats();

    size_t heap_before = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    aetus_test_runtime_hooks_t hooks_before;
    aetus_test_get_runtime_hooks(&hooks_before);
    printf("AETUS_RUNTIME_CONCURRENCY_START\n");
    fflush(stdout);

    ESP_ERROR_CHECK(xTaskCreate(flush_stress_task, "aetus_flush_stress", 4096, NULL, 2, NULL) == pdPASS ? ESP_OK : ESP_FAIL);
    for (uint32_t i = 0; i < STRESS_TELEMETRY_TASKS; i++) {
        ESP_ERROR_CHECK(xTaskCreate(telemetry_stress_task, "aetus_tel_stress", 4096, (void *)(uintptr_t)i, 4, NULL) == pdPASS ? ESP_OK : ESP_FAIL);
    }
    for (uint32_t i = 0; i < STRESS_SIGNAL_TASKS; i++) {
        ESP_ERROR_CHECK(xTaskCreate(signal_stress_task, "aetus_sig_stress", 4096, (void *)(uintptr_t)i, 4, NULL) == pdPASS ? ESP_OK : ESP_FAIL);
    }
    for (uint32_t i = 0; i < STRESS_STATUS_TASKS; i++) {
        ESP_ERROR_CHECK(xTaskCreate(status_stress_task, "aetus_sta_stress", 4096, (void *)(uintptr_t)i, 4, NULL) == pdPASS ? ESP_OK : ESP_FAIL);
    }

    int waited_ms = 0;
    while (__atomic_load_n(&s_stress_tasks_done, __ATOMIC_SEQ_CST) < total_tasks && waited_ms < 10000) {
        vTaskDelay(pdMS_TO_TICKS(50));
        waited_ms += 50;
    }
    s_stress_stop_flush = true;
    while (__atomic_load_n(&s_stress_flush_done, __ATOMIC_SEQ_CST) == 0U && waited_ms < 12000) {
        vTaskDelay(pdMS_TO_TICKS(50));
        waited_ms += 50;
    }

    printf(
        "AETUS_RUNTIME_CONCURRENCY_TASKS_JOINED done=%lu/%lu flush_done=%lu waited_ms=%d\n",
        (unsigned long)__atomic_load_n(&s_stress_tasks_done, __ATOMIC_SEQ_CST),
        (unsigned long)total_tasks,
        (unsigned long)__atomic_load_n(&s_stress_flush_done, __ATOMIC_SEQ_CST),
        waited_ms
    );
    fflush(stdout);

    ESP_ERROR_CHECK(aetus_flush(pdMS_TO_TICKS(1000)));
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_ERROR_CHECK(aetus_flush(pdMS_TO_TICKS(1000)));

    aetus_signal_sample_pool_stats_t signal_stats;
    aetus_test_release_stats_t telemetry_stats;
    aetus_test_runtime_hooks_t hooks;
    ESP_ERROR_CHECK(aetus_get_signal_sample_pool_stats(&signal_stats));
    aetus_test_get_release_stats(&telemetry_stats);
    aetus_test_get_runtime_hooks(&hooks);

    size_t heap_after = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    size_t heap_delta = heap_before > heap_after ? heap_before - heap_after : heap_after - heap_before;
    bool heap_ok = heap_caps_check_integrity_all(true);
    uint32_t post_delta = hooks.fake_post_count - hooks_before.fake_post_count;
    bool pass =
        __atomic_load_n(&s_stress_tasks_done, __ATOMIC_SEQ_CST) == total_tasks &&
        __atomic_load_n(&s_stress_flush_done, __ATOMIC_SEQ_CST) == 1U &&
        signal_stats.allocated_blocks == 0U &&
        signal_stats.allocated_bytes == 0U &&
        telemetry_stats.telemetry_heap_metrics_released > 0U &&
        post_delta > 0U &&
        heap_ok;

    printf(
        "AETUS_RUNTIME_CONCURRENCY_STATS done=%lu/%lu flush_done=%lu signal_blocks=%lu signal_bytes=%u telemetry_heap_released=%lu post_delta=%lu heap_delta=%u heap_ok=%d\n",
        (unsigned long)__atomic_load_n(&s_stress_tasks_done, __ATOMIC_SEQ_CST),
        (unsigned long)total_tasks,
        (unsigned long)__atomic_load_n(&s_stress_flush_done, __ATOMIC_SEQ_CST),
        (unsigned long)signal_stats.allocated_blocks,
        (unsigned)signal_stats.allocated_bytes,
        (unsigned long)telemetry_stats.telemetry_heap_metrics_released,
        (unsigned long)post_delta,
        (unsigned)heap_delta,
        heap_ok ? 1 : 0
    );
    print_result("CONCURRENCY", pass);
    return pass;
}

static bool test_time_failure_survives(void)
{
    apply_hooks(ESP_OK, ESP_FAIL, true);
    esp_err_t sync_err = aetus_sync_rtc(pdMS_TO_TICKS(1000));
    esp_err_t enqueue_err = enqueue_dynamic_telemetry(9000U, pdMS_TO_TICKS(100));
    esp_err_t flush_err = aetus_flush(pdMS_TO_TICKS(10000));

    bool pass = sync_err != ESP_OK && enqueue_err == ESP_OK && flush_err == ESP_OK;
    printf(
        "AETUS_RUNTIME_TIME_FAIL_STATS sync=%s enqueue=%s flush=%s\n",
        esp_err_to_name(sync_err),
        esp_err_to_name(enqueue_err),
        esp_err_to_name(flush_err)
    );
    print_result("TIME_FAIL_SURVIVES", pass);
    return pass;
}

void app_main(void)
{
    printf("AETUS_RUNTIME_APP_MAIN\n");
    fflush(stdout);

    apply_hooks(ESP_OK, ESP_OK, false);
    aetus_config_t config = {
        .wifi_ssid = "qemu-ssid",
        .wifi_password = "qemu-password",
        .wifi_auth = AETUS_WIFI_AUTH_PSK,
        .ingest_url = "http://127.0.0.1:18000/v1/ingest",
        .time_url = "http://127.0.0.1:18000/v1/time",
        .device_id = "qemu-runtime-device",
        .device_token = "qemu-runtime-token",
        .auth_mode = AETUS_AUTH_BEARER,
        .firmware_version = 1,
        .upload_interval_ms = 3600000U,
        .queue_depth = TEST_QUEUE_DEPTH,
    };
    ESP_ERROR_CHECK(aetus_start(&config));

    bool all_pass = true;
    all_pass = test_telemetry_full_release() && all_pass;
    all_pass = test_signal_enqueue_failure_release() && all_pass;
    all_pass = test_concurrency_stress() && all_pass;
    all_pass = test_time_failure_survives() && all_pass;

    print_result("ALL", all_pass);
    printf("AETUS_RUNTIME_TEST_DONE\n");
    fflush(stdout);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
