idf_component_register(
    SRCS
        "main.c"
        "../../../esp32-aetus/components/aetus/aetus_queue_item.c"
    INCLUDE_DIRS
        "."
        "../../../esp32-aetus/components/aetus/include"
    REQUIRES
        heap
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE CONFIG_AETUS_TEST_HOOKS=1)
