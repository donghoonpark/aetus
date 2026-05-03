set(AETUS_WIFI_SSID "$ENV{AETUS_WIFI_SSID}")
set(AETUS_WIFI_PASSWORD "$ENV{AETUS_WIFI_PASSWORD}")
set(AETUS_WIFI_AUTH "$ENV{AETUS_WIFI_AUTH}")
set(AETUS_WIFI_ID "$ENV{AETUS_WIFI_ID}")
set(AETUS_INGEST_URL "$ENV{AETUS_INGEST_URL}")
set(AETUS_TIME_URL "$ENV{AETUS_TIME_URL}")
set(AETUS_DEVICE_ID "$ENV{AETUS_DEVICE_ID}")
set(AETUS_DEVICE_TOKEN "$ENV{AETUS_DEVICE_TOKEN}")
set(AETUS_UPLOAD_INTERVAL_MS "$ENV{AETUS_UPLOAD_INTERVAL_MS}")
set(AETUS_SAMPLE_INTERVAL_MS "$ENV{AETUS_SAMPLE_INTERVAL_MS}")

if(NOT AETUS_WIFI_AUTH)
    set(AETUS_WIFI_AUTH "psk")
endif()

if(NOT AETUS_UPLOAD_INTERVAL_MS)
    set(AETUS_UPLOAD_INTERVAL_MS "60000")
endif()

if(NOT AETUS_SAMPLE_INTERVAL_MS)
    set(AETUS_SAMPLE_INTERVAL_MS "30000")
endif()

foreach(required_var
    AETUS_WIFI_SSID
    AETUS_WIFI_PASSWORD
    AETUS_INGEST_URL
    AETUS_DEVICE_ID
    AETUS_DEVICE_TOKEN
)
    if(NOT DEFINED ${required_var} OR "${${required_var}}" STREQUAL "")
        message(FATAL_ERROR "${required_var} environment variable is required")
    endif()
endforeach()

string(TOLOWER "${AETUS_WIFI_AUTH}" AETUS_WIFI_AUTH_NORMALIZED)
if(AETUS_WIFI_AUTH_NORMALIZED STREQUAL "peap")
    set(AETUS_WIFI_AUTH_PEAP 1)
    if(NOT AETUS_WIFI_ID)
        message(FATAL_ERROR "AETUS_WIFI_ID environment variable is required when AETUS_WIFI_AUTH=peap")
    endif()
elseif(AETUS_WIFI_AUTH_NORMALIZED STREQUAL "psk")
    set(AETUS_WIFI_AUTH_PEAP 0)
else()
    message(FATAL_ERROR "AETUS_WIFI_AUTH must be either 'psk' or 'peap'")
endif()

if(NOT AETUS_TIME_URL)
    string(REPLACE "/v1/ingest" "/v1/time" AETUS_TIME_URL "${AETUS_INGEST_URL}")
endif()

configure_file(
    "${COMPONENT_DIR}/aetus_config.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/aetus_config.h"
    @ONLY
)

idf_component_register(
    SRCS
        "main.cpp"
    INCLUDE_DIRS
        "."
        "${CMAKE_CURRENT_BINARY_DIR}"
    REQUIRES
        aetus
        esp_pm
        esp_timer
        esp_wifi
)

target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++20)
