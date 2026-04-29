set(AETUS_WIFI_SSID "$ENV{AETUS_WIFI_SSID}")
set(AETUS_WIFI_PASSWORD "$ENV{AETUS_WIFI_PASSWORD}")
set(AETUS_INGEST_URL "$ENV{AETUS_INGEST_URL}")
set(AETUS_DEVICE_ID "$ENV{AETUS_DEVICE_ID}")
set(AETUS_DEVICE_TOKEN "$ENV{AETUS_DEVICE_TOKEN}")
set(AETUS_UPLOAD_INTERVAL_MS "$ENV{AETUS_UPLOAD_INTERVAL_MS}")

if(NOT AETUS_UPLOAD_INTERVAL_MS)
    set(AETUS_UPLOAD_INTERVAL_MS "600000")
endif()

foreach(required_var
    AETUS_WIFI_SSID
    AETUS_WIFI_PASSWORD
    AETUS_INGEST_URL
    AETUS_DEVICE_ID
    AETUS_DEVICE_TOKEN
)
    if(NOT ${required_var})
        message(FATAL_ERROR "${required_var} environment variable is required")
    endif()
endforeach()

configure_file(
    "${COMPONENT_DIR}/aetus_config.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/aetus_config.h"
    @ONLY
)

idf_component_register(
    SRCS
        "main.c"
    INCLUDE_DIRS
        "${CMAKE_CURRENT_BINARY_DIR}"
    REQUIRES
        aetus_uploader
)
