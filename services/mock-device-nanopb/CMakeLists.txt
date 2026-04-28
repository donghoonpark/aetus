cmake_minimum_required(VERSION 3.24)

project(mock_device_nanopb C CXX)

include(FetchContent)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(INGEST_PROTO "" CACHE FILEPATH "Path to ingest.proto")
if(NOT INGEST_PROTO)
    message(FATAL_ERROR "INGEST_PROTO must be provided")
endif()

FetchContent_Declare(
    nanopb
    GIT_REPOSITORY https://github.com/nanopb/nanopb.git
    GIT_TAG nanopb-0.4.9.1
)

FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.13.6
)

FetchContent_MakeAvailable(nanopb)
FetchContent_MakeAvailable(pybind11)

set(GENERATED_DIR ${CMAKE_BINARY_DIR}/generated)
file(MAKE_DIRECTORY ${GENERATED_DIR})

get_filename_component(INGEST_PROTO_DIR ${INGEST_PROTO} DIRECTORY)
set(NANOPB_PLUGIN ${nanopb_SOURCE_DIR}/generator/protoc-gen-nanopb)
set(NANOPB_OPTIONS ${CMAKE_CURRENT_SOURCE_DIR}/proto/ingest.options)

add_custom_command(
    OUTPUT ${GENERATED_DIR}/ingest.pb.c ${GENERATED_DIR}/ingest.pb.h
    COMMAND ${Python3_EXECUTABLE} -m grpc_tools.protoc
        -I ${CMAKE_CURRENT_SOURCE_DIR}/proto
        -I ${INGEST_PROTO_DIR}
        --plugin=protoc-gen-nanopb=${NANOPB_PLUGIN}
        --nanopb_out=-f${NANOPB_OPTIONS}:${GENERATED_DIR}
        ${INGEST_PROTO}
    DEPENDS ${INGEST_PROTO} ${NANOPB_OPTIONS}
    VERBATIM
)

add_library(ingest_proto STATIC
    ${GENERATED_DIR}/ingest.pb.c
)

add_library(nanopb_runtime STATIC
    ${nanopb_SOURCE_DIR}/pb_common.c
    ${nanopb_SOURCE_DIR}/pb_encode.c
)

target_include_directories(nanopb_runtime PUBLIC
    ${nanopb_SOURCE_DIR}
)

target_include_directories(ingest_proto PUBLIC
    ${GENERATED_DIR}
    ${nanopb_SOURCE_DIR}
)

target_link_libraries(ingest_proto PUBLIC nanopb_runtime)

add_library(mock_device_core STATIC
    src/mock_device_core.c
)

target_include_directories(mock_device_core PUBLIC
    ${GENERATED_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(mock_device_core PUBLIC ingest_proto nanopb_runtime)

add_executable(mock_device
    src/mock_device_main.c
)

target_link_libraries(mock_device PRIVATE mock_device_core)

pybind11_add_module(mock_device_py src/mock_device_pybind.cpp)
target_link_libraries(mock_device_py PRIVATE mock_device_core)
target_include_directories(mock_device_py PRIVATE
    ${GENERATED_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)
set_target_properties(mock_device_py PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/python
)
