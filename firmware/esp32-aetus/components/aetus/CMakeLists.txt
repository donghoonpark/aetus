set(AETUS_SRCS
    "aetus.c"
    "aetus_queue_item.c"
    "aetus_signal_sample_pool.c"
    "ingest.pb.c"
)

set(AETUS_REQUIRES
    esp_event
    esp_http_client
    esp_netif
    esp_timer
    esp_wifi
    esp_driver_gpio
    mbedtls
    nanopb
    nvs_flash
    wpa_supplicant
)

if(CONFIG_BT_NIMBLE_ENABLED)
    list(APPEND AETUS_SRCS "aetus_provisioning.c")
else()
    list(APPEND AETUS_SRCS "aetus_provisioning_stub.c")
endif()

idf_component_register(
    SRCS
        ${AETUS_SRCS}
    INCLUDE_DIRS
        "include"
    REQUIRES
        ${AETUS_REQUIRES}
)

if(CONFIG_BT_NIMBLE_ENABLED)
    idf_component_optional_requires(PRIVATE bt)
endif()
