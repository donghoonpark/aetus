#include <stddef.h>
#include <stdint.h>

#include "aetus.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"

#define CONTRACT_SAMPLE_RATE_HZ 200U
#define CONTRACT_DURATION_S 3U
#define CONTRACT_CHANNEL_COUNT 2U
#define CONTRACT_SAMPLE_WIDTH_BYTES 2U
#define CONTRACT_SAMPLE_COUNT (CONTRACT_SAMPLE_RATE_HZ * CONTRACT_DURATION_S)
#define CONTRACT_SAMPLE_BYTES (CONTRACT_SAMPLE_COUNT * CONTRACT_CHANNEL_COUNT * CONTRACT_SAMPLE_WIDTH_BYTES)

AETUS_STATIC_ASSERT(CONTRACT_SAMPLE_COUNT == 600U, "contract sample count changed unexpectedly");
AETUS_STATIC_ASSERT(CONTRACT_SAMPLE_BYTES == 2400U, "contract sample byte size changed unexpectedly");
AETUS_STATIC_ASSERT(
    sizeof(aetus_signal_frame_t) <= AETUS_SIGNAL_FRAME_STRUCT_MAX_BYTES,
    "signal frame metadata must stay small; sample bytes belong in the pool"
);
AETUS_STATIC_ASSERT(
    AETUS_SIGNAL_SAMPLES_MAX >= CONTRACT_SAMPLE_BYTES,
    "AETUS_SIGNAL_SAMPLES_MAX must cover the 200Hz/3s/2ch/int16 signal frame contract"
);
static void build_contract_frame(void)
{
    static const uint8_t samples[CONTRACT_SAMPLE_BYTES] = {0};

    aetus_signal_frame_t frame;
    aetus_signal_frame_init(&frame);
    frame.sample_interval_ns = 5000000ULL;
    frame.sample_count = CONTRACT_SAMPLE_COUNT;
    frame.encoding = AETUS_SIGNAL_ENCODING_INT16_LE;
    frame.layout = AETUS_SIGNAL_LAYOUT_INTERLEAVED;

    ESP_ERROR_CHECK(aetus_signal_frame_set_stream_key(&frame, "contract.vibration"));
    ESP_ERROR_CHECK(aetus_signal_frame_add_channel(&frame, "axis_x", "raw", NULL, NULL));
    ESP_ERROR_CHECK(aetus_signal_frame_add_channel(&frame, "axis_y", "raw", NULL, NULL));
    ESP_ERROR_CHECK(aetus_signal_frame_set_samples(&frame, samples, sizeof(samples)));
    ESP_ERROR_CHECK(frame.samples == samples ? ESP_OK : ESP_FAIL);
}

void app_main(void)
{
    build_contract_frame();
}
