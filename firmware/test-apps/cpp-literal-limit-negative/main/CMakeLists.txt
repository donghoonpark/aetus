idf_component_register(
    SRCS
        "main.cpp"
    INCLUDE_DIRS
        "."
    REQUIRES
        aetus
)

target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++20)
