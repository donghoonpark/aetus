cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../esp32-aetus/components/aetus"
    "${CMAKE_CURRENT_LIST_DIR}/../esp32-aetus/components/nanopb"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(aetus_esp32c5_upload_smoke)
