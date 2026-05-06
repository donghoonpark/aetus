#include "aetus.hpp"

extern "C" void app_main(void)
{
    aetus::Telemetry telemetry;
    (void)telemetry.add_double("metric_key_over_twenty", 1.0);
}
