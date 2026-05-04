cmake_minimum_required(VERSION 3.16)

# This repository is not a standalone CMake build. The root entry point exists
# so external ESP-IDF apps can import AETUS with FetchContent_MakeAvailable().
add_subdirectory(firmware/esp32-aetus)
