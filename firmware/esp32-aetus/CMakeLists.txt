cmake_minimum_required(VERSION 3.16)

include_guard(GLOBAL)

get_filename_component(AETUS_ESP_IDF_ROOT "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
set(
    AETUS_ESP_IDF_COMPONENT_DIRS
    "${AETUS_ESP_IDF_ROOT}/components/aetus"
    "${AETUS_ESP_IDF_ROOT}/components/nanopb"
    CACHE INTERNAL "AETUS ESP-IDF component directories"
)

function(aetus_get_component_dirs out_var)
    set(${out_var} "${AETUS_ESP_IDF_COMPONENT_DIRS}" PARENT_SCOPE)
endfunction()

function(aetus_append_component_dirs)
    set(one_value_args TARGET_VARIABLE)
    cmake_parse_arguments(AETUS_APPEND "" "${one_value_args}" "" ${ARGN})

    if(AETUS_APPEND_TARGET_VARIABLE)
        set(target_variable "${AETUS_APPEND_TARGET_VARIABLE}")
    else()
        set(target_variable EXTRA_COMPONENT_DIRS)
    endif()

    set(component_dirs ${${target_variable}} ${AETUS_ESP_IDF_COMPONENT_DIRS})
    list(REMOVE_DUPLICATES component_dirs)
    set(${target_variable} "${component_dirs}" PARENT_SCOPE)
endfunction()
