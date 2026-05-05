idf_component_register(SRCS "main.c"
                    INCLUDE_DIRS
                        "."
                        "../../../esp32-aetus/components/aetus/include"
                    REQUIRES driver esp_driver_gptimer)
