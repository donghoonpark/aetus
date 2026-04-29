cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../../components/aetus"
    "${CMAKE_CURRENT_LIST_DIR}/../../components/nanopb"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(aetus_cpp_basic)
